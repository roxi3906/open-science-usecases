"""Bounded case scheduling and the single manifest commit monitor."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, CancelledError
import copy
import json
import os
from pathlib import Path
import threading
import time

from publication_steps import Reporter, atomic_json, digest, encoded, print_plan, write_artifacts


def bounded_map(pool, function, items, limit, stopped):
    """At most limit outstanding futures per caller, with failure drain before return."""
    iterator = iter(items)
    pending = {}
    results = []
    error = None
    try:
        while True:
            while len(pending) < limit and not stopped.is_set():
                item = next(iterator, None)
                if item is None:
                    break
                pending[pool.submit(function, item)] = item
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future)
                try:
                    results.append(future.result())
                except BaseException as caught:
                    # Cancellation is a consequence; retain the actual worker failure.
                    if error is None or isinstance(error, CancelledError):
                        error = caught
                    stopped.set()
            if stopped.is_set():
                for future in pending:
                    future.cancel()
        if error:
            raise error
        if stopped.is_set():
            raise CancelledError('Publication stopped after a concurrent failure')
        return results
    finally:
        for future in pending:
            future.cancel()
        wait(pending)


def publish_case(root, case, store, uploads, heads, stopped, reporter, task, upload_workers):
    from publish import file_integrity, matching_head
    started = time.monotonic()
    objects = case['objects']
    repaired = 0
    cleaned = 0
    reporter.event(task, 'case', 'running', name=case['name'], objects=len(objects))

    def upload(obj, action='upload'):
        if stopped.is_set():
            raise CancelledError()
        path = root / obj['local_path']
        try:
            if (not path.is_file() or any(p.is_symlink() for p in (path, *path.parents))
                    or file_integrity(path) != (obj['bytes'], obj['sha256'])):
                raise ValueError('Staged file size or checksum mismatch: ' + obj['target_key'])
            store.upload(path, obj['target_key'])
            reporter.event(task, 'file', 'completed', **obj, transfer_action=action)
        except BaseException as error:
            stopped.set()
            reporter.event(task, 'file', 'failed', **obj, transfer_action=action, error=str(error))
            raise

    def head(obj):
        if stopped.is_set():
            raise CancelledError()
        try:
            actual = store.head(obj['target_key'])
            matches = matching_head(actual, obj)
            reporter.event(task, 'head', 'completed' if matches else 'mismatch',
                           key=obj['target_key'], expected={'bytes': obj['bytes'], 'sha256': obj['sha256']},
                           actual=None if actual is None else {
                               'ContentLength': actual.get('ContentLength'), 'Metadata': actual.get('Metadata', {})})
            return obj, matches
        except BaseException as error:
            stopped.set()
            reporter.event(task, 'head', 'failed', key=obj['target_key'], error=str(error))
            raise

    try:
        bounded_map(uploads, upload, objects, upload_workers, stopped)
        mismatches = objects
        for round_number in range(3):
            results = bounded_map(heads, head, mismatches, 32, stopped)
            mismatches = [obj for obj, matches in results if not matches]
            if not mismatches:
                break
            if round_number == 2:
                raise ValueError('Uploaded object metadata mismatch after 2 repair rounds')
            repaired += len(mismatches)
            bounded_map(uploads, lambda obj: upload(obj, 'replace'), mismatches, upload_workers, stopped)
        directory = case['directory']
        if directory:
            prefix = directory['target_prefix']
            remote = set(store.list_keys(prefix))
            if any(not key.startswith(prefix) for key in remote):
                raise ValueError('Cleanup key outside exact extracted prefix')
            stale = sorted(remote - {obj['target_key'] for obj in directory['objects']})
            if stopped.is_set():
                raise CancelledError()
            if stale:
                store.delete_keys(stale)
            cleaned = len(stale)
            for key in stale:
                reporter.event(task, 'cleanup_object', 'completed', key=key)
        reporter.event(task, 'case', 'ready_to_commit', name=case['name'], success=len(objects),
                       failures=0, repairs=repaired, cleaned=cleaned,
                       elapsed_seconds=round(time.monotonic() - started, 3))
        return {'success': len(objects), 'repairs': repaired, 'cleaned': cleaned}
    except BaseException as error:
        stopped.set()
        reporter.event(task, 'case', 'failed', name=case['name'], error=str(error), failures=1,
                       repairs=repaired, cleaned=cleaned, elapsed_seconds=round(time.monotonic() - started, 3))
        raise


def apply_task(entries, task):
    before = task['before'] if isinstance(task['before'], list) else [task['before']]
    after = task['after'] if isinstance(task['after'], list) else [task['after']]
    removed = {e['name'] for e in before if e} - {e['name'] for e in after if e}
    entries = [copy.deepcopy(e) for e in entries if e['name'] not in removed]
    for entry in after:
        if not entry:
            continue
        index = next((i for i, old in enumerate(entries) if old['name'] == entry['name']), None)
        if index is None:
            entries.insert(0, copy.deepcopy(entry))
        else:
            entries[index] = copy.deepcopy(entry)
    return entries


def references(entries):
    return {e['name'] + '/' + e[k]['file_name'] for e in entries
            for k in ('cover', 'case', 'introduction') if k in e}


def old_keys(tasks):
    keys = set()
    for task in tasks:
        before = task['before'] if isinstance(task['before'], list) else [task['before']]
        after = task['after'] if isinstance(task['after'], list) else [task['after']]
        old = {e['name']: e for e in before if e}
        for entry in after:
            if not entry:
                continue
            for kind in ('cover', 'introduction'):
                previous = old.get(entry['name'], {}).get(kind)
                if previous and previous['file_name'] != entry[kind]['file_name']:
                    keys.add(entry['name'] + '/' + previous['file_name'])
    return sorted(keys)


def postprocess(store, batch, path):
    from publish import read_manifest
    current = store.read_manifest()
    entries = read_manifest(current, 'current S3 manifest')
    # A later publication may reference a key that was previously obsolete.
    keys = [key for key in batch['cleanup_pending'] if key not in references(entries)]
    if keys:
        store.delete_keys(keys)
    batch['cleanup_pending'] = []
    atomic_json(path, batch)
    if batch['cdn_pending']:
        if hasattr(store, 'refresh_paths'):
            store.refresh_paths(batch['cdn_pending'])
        else:
            for name in batch['cdn_names'] or [None]:
                store.refresh_cdn(name)
        batch['cdn_pending'] = []
        atomic_json(path, batch)


def recover(store, output, root=None):
    """Resume only durable postprocessing intents, never uploads or manifest writes."""
    from publish import read_manifest
    from manifest_diff import git, commit_exists
    from publication_steps import build_steps
    from publish import candidate_reasons
    root = root or Path.cwd()
    plan = json.loads((output / 'plan.json').read_text())
    commit_exists(root, plan['after'])
    target_text = git(root, 'show', plan['after'] + ':manifest.json')
    old = read_manifest(plan['baseline'], 'recovery baseline') if plan['baseline'] is not None else []
    new = read_manifest(target_text, 'fixed recovery target', legacy=False)
    if (output / 'baseline-manifest.json').read_bytes() != (plan['baseline'] or '[]').encode():
        raise ValueError('Recovery baseline evidence mismatch')
    previous = {e['name']: e for e in old}
    changed = {e['name'] for e in new if any(candidate_reasons(
        previous.get(e['name'], {}).get(kind), e[kind])[0] for kind in ('cover', 'case', 'introduction'))}
    steps = build_steps(old, new, target_text)
    canonical = []
    for step in steps:
        if step['operation'] not in ('ADD_CASE', 'UPDATE_CASE', 'UPDATE_CATALOG'):
            continue
        entries = step['after'] if isinstance(step['after'], list) else [step['after']]
        names = {e['name'] for e in entries if e}
        if names & changed:
            canonical.append(step)
    expected_batches = [canonical[i:i + 2] for i in range(0, len(canonical), 2)] or [[]]
    paths = sorted((output / 'batches').glob('*/status.json'))
    if not paths:
        raise ValueError('No batch recovery evidence found')
    expected_manifest = old
    for number, path in enumerate(paths):
        if number >= len(expected_batches) or path.parent.name != '%03d' % (number + 1):
            raise ValueError('Recovery batch sequence differs from fixed target')
        tasks = expected_batches[number]
        for task in tasks:
            expected_manifest = apply_task(expected_manifest, task)
        expected_text = target_text if number == len(expected_batches) - 1 else encoded(expected_manifest)
        batch = json.loads(path.read_text())
        if len(tasks) != len(batch['tasks']) or any(
                any(actual.get(field) != expected[field] for field in ('id', 'operation', 'name', 'before', 'after'))
                for actual, expected in zip(batch['tasks'], tasks)):
            raise ValueError('Recovery tasks differ from fixed target')
        names = sorted({e['name'] for task in tasks for e in (
            task['after'] if isinstance(task['after'], list) else [task['after']]) if e and e['name'] in changed})
        if batch['cdn_names'] != names:
            raise ValueError('Recovery cases differ from fixed target')
        if (path.parent / 'manifest.json').read_bytes() != expected_text.encode():
            raise ValueError('Recovery batch manifest differs from fixed target')
        if not batch['cleanup_pending'] and not batch['cdn_pending']:
            continue
        if batch['target'] != store.target or batch['distribution'] != os.environ.get('AWS_DISTRIBUITION_ID', ''):
            raise ValueError('Recovery target or CDN distribution differs from evidence')
        text = (path.parent / 'manifest.json').read_bytes().decode('utf-8')
        if digest(text) != batch['manifest_sha256']:
            raise ValueError('Recovery manifest identity mismatch')
        read_manifest(text, 'committed recovery manifest')
        current = store.read_manifest()
        if not batch['manifest_verified'] and current != text:
            raise ValueError('Cannot establish that the recovery manifest was committed')
        if batch['manifest_committed'] is False:
            # Includes definite conditional conflicts, even if the bytes happen to match.
            raise ValueError('Recovery batch was not committed')
        if not set(batch['cleanup_pending']).issubset(old_keys(tasks)):
            raise ValueError('Recovery cleanup differs from renamed resources')
        if batch['cdn_pending'] and batch['cdn_pending'] != store.cdn_paths(batch['cdn_names']):
            raise ValueError('Recovery CDN paths differ from recorded cases')
        batch['manifest_committed'] = batch['manifest_verified'] = True
        atomic_json(path, batch)
        postprocess(store, batch, path)
    print('Postprocessing recovery completed; no resources or manifests uploaded.', flush=True)


def execute(root, plan, store, output):
    from publish import read_manifest
    from s3_storage import uncertain_write
    workers = int(os.environ.get('PUBLICATION_UPLOAD_WORKERS', '16'))
    if not 1 <= workers <= 64:
        raise ValueError('PUBLICATION_UPLOAD_WORKERS must be between 1 and 64')
    write_artifacts(plan, output)
    reporter = Reporter(plan, output)
    print_plan(plan)
    states = {s['id']: s for s in reporter.state['steps']}
    tasks = plan['tasks']
    stopped = threading.Event()
    baseline = plan['baseline']
    batch_number = 0
    committed_count = 0
    ready = {}
    failure = None

    def commit(batch_tasks, final=False):
        nonlocal baseline, batch_number, committed_count
        if stopped.is_set():
            raise CancelledError('No manifest commits after task failure')
        entries = json.loads(baseline or '[]')
        for task in batch_tasks:
            entries = apply_task(entries, task)
        text = plan['steps'][-1]['manifest'] if final else encoded(entries)
        read_manifest(text, 'batch manifest', legacy=not final)
        if store.read_manifest() != baseline:
            raise ValueError('S3 manifest changed since preparation; dispatch a fresh run')
        batch_number += 1
        directory = output / 'batches' / ('%03d' % batch_number)
        directory.mkdir(parents=True, exist_ok=True)
        manifest_path = directory / 'manifest.json'
        manifest_path.write_bytes(text.encode('utf-8'))
        names = sorted({case['name'] for task in batch_tasks for case in task['cases'] if case['objects']})
        paths = store.cdn_paths(names) if hasattr(store, 'cdn_paths') else ['manifest.json'] + names
        batch = {'tasks': batch_tasks, 'target': getattr(store, 'target', None),
                 'distribution': os.environ.get('AWS_DISTRIBUITION_ID', ''),
                 'manifest_sha256': digest(text), 'manifest_etag': None,
                 'manifest_committed': None, 'manifest_verified': False,
                 'cleanup_pending': old_keys(batch_tasks), 'cdn_names': names, 'cdn_pending': paths}
        status_path = directory / 'status.json'
        atomic_json(status_path, batch)
        marker = batch_tasks[-1] if batch_tasks else plan['steps'][-1]
        # Index-only batches still need acceptance and verification evidence.
        selected = [states[t['id']] for t in batch_tasks] or [states[marker['id']]]
        reporter.event(marker, 'manifest', 'running', batch=batch_number, tasks=[t['id'] for t in batch_tasks], final=final)
        try:
            if stopped.is_set():
                raise CancelledError()
            try:
                store.commit_manifest(manifest_path)
                batch['manifest_committed'] = True
            except Exception as error:
                if not uncertain_write(error):
                    batch['manifest_committed'] = False
                    raise
                if store.read_manifest() != text:
                    raise
                batch['manifest_committed'] = True
            finally:
                atomic_json(status_path, batch)
                for state in selected:
                    state['manifest_committed'] = batch['manifest_committed']
                reporter.save()
            if store.read_manifest() != text:
                raise ValueError('Published manifest read-back mismatch')
            baseline = text
            batch.update(manifest_verified=True, manifest_etag=getattr(store, 'etag', None))
            atomic_json(status_path, batch)
            for state in selected:
                state.update(status='manifest_committed', manifest_verified=True)
            reporter.save()
            reporter.event(marker, 'manifest', 'completed', batch=batch_number)
            reporter.event(marker, 'postprocess', 'running', cleanup=len(batch['cleanup_pending']), paths=len(paths))
            postprocess(store, batch, status_path)
            for state in selected:
                state.update(status='completed', cdn='completed')
            committed_count += len(batch_tasks)
            reporter.event(marker, 'postprocess', 'completed', batch=batch_number, tasks=len(batch_tasks))
        except BaseException:
            for state in selected:
                state.update(status='failed', cleanup_pending=batch['cleanup_pending'], cdn_pending=batch['cdn_pending'])
                if batch['manifest_verified']:
                    state['cdn'] = 'pending' if batch['cdn_pending'] else 'completed'
            reporter.save()
            raise

    try:
        if store.read_manifest() != baseline:
            raise ValueError('S3 manifest changed since preparation; dispatch a fresh run')
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='upload') as uploads, \
             ThreadPoolExecutor(max_workers=32, thread_name_prefix='head') as heads, \
             ThreadPoolExecutor(max_workers=4, thread_name_prefix='task') as outer:
            def run_task(task):
                try:
                    if len(task['cases']) > 1:
                        with ThreadPoolExecutor(max_workers=4, thread_name_prefix='case') as cases:
                            return bounded_map(cases, lambda case: publish_case(
                                root, case, store, uploads, heads, stopped, reporter, task, workers),
                                task['cases'], 4, stopped)
                    return [publish_case(root, task['cases'][0], store, uploads, heads,
                                         stopped, reporter, task, workers)]
                except BaseException:
                    stopped.set()
                    raise

            running = {}
            next_task = 0
            while next_task < len(tasks) or running:
                # Limit the reorder buffer as well as executor submissions.
                while not stopped.is_set() and next_task < len(tasks) and len(running) < 4 and next_task - committed_count < 4:
                    task = tasks[next_task]
                    exclusive = len(task['cases']) > 1
                    if (exclusive and running) or any(len(t['cases']) > 1 for t in running.values()):
                        break
                    states[task['id']]['status'] = 'running'
                    reporter.event(task, 'task', 'running', cases=len(task['cases']))
                    running[outer.submit(run_task, task)] = task
                    next_task += 1
                    if exclusive:
                        break
                reporter.save()
                if not running:
                    if stopped.is_set():
                        break
                    raise ValueError('Task monitor could not make progress')
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                # Inspect every already finished result before any manifest write.
                done |= {future for future in running if future.done()}
                for future in done:
                    task = running.pop(future)
                    state = states[task['id']]
                    try:
                        result = future.result()
                        ready[task['id']] = task
                        state['status'] = 'ready_to_commit'
                        for file in state['files']:
                            file['status'] = 'completed'
                        reporter.event(task, 'task', 'ready_to_commit', counts=result)
                    except BaseException as error:
                        stopped.set()
                        if failure is None or isinstance(failure, CancelledError):
                            failure = error
                        state['status'] = 'failed'
                        reporter.event(task, 'task', 'failed', error=str(error))
                if stopped.is_set():
                    for future in running:
                        future.cancel()
                    continue
                while committed_count < len(tasks):
                    batch_tasks = tasks[committed_count:committed_count + 2]
                    if not all(t['id'] in ready for t in batch_tasks):
                        break
                    try:
                        commit(batch_tasks, final=committed_count + len(batch_tasks) == len(tasks))
                    except BaseException as error:
                        stopped.set()
                        failure = error
                        for future in running:
                            future.cancel()
                        break
            if failure:
                raise failure
            if stopped.is_set():
                raise CancelledError('Publication stopped')
            if not tasks:
                commit([], final=True)
        for state in reporter.state['steps']:
            if state['status'] == 'waiting':
                state.update(status='completed', manifest_committed=True, manifest_verified=True, cdn='completed')
        reporter.state['status'] = 'completed'
        print('Publication completed: tasks={} batches={}'.format(len(tasks), batch_number), flush=True)
    except BaseException as error:
        stopped.set()
        reporter.state['status'] = 'failed'
        reporter.state['error'] = str(error)
        reporter.state['uncommitted_tasks'] = [s['id'] for s in states.values()
                                                if s['status'] != 'completed' and not s['manifest_committed']]
        print('Publication failed: {}; in-flight writes may remain; inspect events and batch recovery evidence.'.format(error), flush=True)
        raise
    finally:
        reporter.close()
        reporter.summary()
