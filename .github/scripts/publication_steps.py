"""Classification-safe task plans and serialized publication evidence."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import queue
import threading
import uuid

from check_structure import catalog_errors


def encoded(value):
    return json.dumps(value, ensure_ascii=True, indent=2) + '\n'


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def build_steps(old, new, target_text):
    """Group connected classification changes before constructing legal checkpoints."""
    previous = {e['name']: e for e in old}
    desired = {e['name']: e for e in new}
    order = ([e['name'] for e in reversed(new) if e['name'] not in previous]
             + [e['name'] for e in new if e['name'] in previous and e != previous[e['name']]]
             + [e['name'] for e in old if e['name'] not in desired])
    groups = [{name} for name in order]
    def labels(name):
        return {e.get('catalog_name') for e in (previous.get(name, {}), desired.get(name, {}))
                if e.get('catalog_name')}
    old_catalogs = {e.get('catalog_name'): e.get('catalog') for e in old if e.get('catalog_name')}
    new_catalogs = {e.get('catalog_name'): e.get('catalog') for e in new if e.get('catalog_name')}
    remapped = {key for key in old_catalogs.keys() & new_catalogs.keys()
                if old_catalogs[key] != new_catalogs[key]}
    def classification_changed(name):
        if labels(name) & remapped:
            return True
        if name not in previous or name not in desired:
            return False
        return tuple(previous.get(name, {}).get(k) for k in ('catalog', 'catalog_name')) != tuple(
            desired.get(name, {}).get(k) for k in ('catalog', 'catalog_name'))
    for name in order:
        if not classification_changed(name):
            continue
        related = [g for g in groups if any(labels(name) & labels(n) for n in g) or name in g]
        merged = set().union(*related)
        first = min(groups.index(g) for g in related)
        groups = [g for g in groups if g not in related]
        groups.insert(first, merged)
    current = copy.deepcopy(old)
    steps = []
    for names in groups:
        before = [e for e in current if e['name'] in names]
        for name in order:
            if name not in names:
                continue
            if name not in desired:
                current = [e for e in current if e['name'] != name]
            elif name in previous:
                index = next(i for i, e in enumerate(current) if e['name'] == name)
                current[index] = copy.deepcopy(desired[name])
            else:
                current.insert(0, copy.deepcopy(desired[name]))
        after = [e for e in current if e['name'] in names]
        if catalog_errors(current, legacy=True):
            raise ValueError('Cannot build publication checkpoints with invalid catalogs')
        name = next(iter(names)) if len(names) == 1 else None
        operation = ('UPDATE_CATALOG' if name is None else
                     'REMOVE_CASE' if not after else 'ADD_CASE' if not before else 'UPDATE_CASE')
        steps.append({'id': len(steps) + 1, 'operation': operation, 'name': name,
                      'before': before if name is None else (before[0] if before else None),
                      'after': after if name is None else (after[0] if after else None),
                      'manifest': encoded(current), 'files': [], 'packages': []})
    if [e['name'] for e in current] != [e['name'] for e in new]:
        steps.append({'id': len(steps) + 1, 'operation': 'REORDER', 'name': None,
                      'before': [e['name'] for e in current], 'after': [e['name'] for e in new],
                      'manifest': encoded(new), 'files': [], 'packages': []})
    steps.append({'id': len(steps) + 1, 'operation': 'FINAL_SYNC', 'name': None,
                  'before': None, 'after': None, 'manifest': target_text, 'files': [], 'packages': []})
    return steps


def group_files(plan):
    """Attach originals and expanded archive files to the same case checkpoint."""
    for step in plan['steps']:
        # Rebuild execution lists exclusively from the validated plan inventory.
        step['files'] = []
        step['packages'] = []
        if step['operation'] in ('ADD_CASE', 'UPDATE_CASE', 'UPDATE_CATALOG'):
            names = ({entry['name'] for entry in step['after']} if step['operation'] == 'UPDATE_CATALOG'
                     else {step['name']})
            step['files'] = [item for item in plan['files']
                             if item['key'].split('/', 1)[0] in names]
            step['packages'] = [item for item in plan['packages'] if item['name'] in names]
    plan['tasks'] = build_tasks(plan)
    return plan


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(encoded(value), encoding='utf-8')
    temporary.replace(path)


def write_artifacts(plan, output):
    batch_paths = list((output / 'batches').glob('*/status.json'))
    for path in batch_paths:
        batch = json.loads(path.read_text())
        if batch.get('manifest_committed') is not False and (batch.get('cleanup_pending') or batch.get('cdn_pending')):
            raise ValueError('Evidence has pending postprocessing; recover it before reusing this output directory')
    status_path = output / 'status.json'
    prior_status = json.loads(status_path.read_text()).get('status') if status_path.exists() else None
    if batch_paths or prior_status in ('completed', 'failed'):
        archive = output.with_name(output.name + '-previous-' + uuid.uuid4().hex)
        archive.mkdir()
        # Preserve previous evidence without moving unrelated files in the output directory.
        for name in ('batches', 'steps', 'events.jsonl', 'status.json', 'summary.md', 'plan.json', 'baseline-manifest.json'):
            source = output / name
            if source.exists():
                source.rename(archive / name)
        print('Archived previous publication evidence: ' + str(archive), flush=True)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'baseline-manifest.json').write_text(plan['baseline'] or '[]', encoding='utf-8')
    for step in plan['steps']:
        directory = output / 'steps' / ('%03d-%s' % (step['id'], step['name'] or step['operation'].lower()))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'manifest.json').write_text(step['manifest'], encoding='utf-8')
        atomic_json(directory / 'files.json', step['files'])
        atomic_json(directory / 'entry-before.json', step['before'])
        atomic_json(directory / 'entry-after.json', step['after'])
    atomic_json(output / 'plan.json', plan)


def print_plan(plan):
    print('Publication plan: target={} tasks={} objects={} packages={}'.format(
        plan['after'], len(plan['tasks']), len(plan['files']) - 1, len(plan['packages'])), flush=True)


def build_tasks(plan):
    from s3_storage import content_type
    old = {e['name']: e for e in json.loads(plan['baseline'] or '[]')}
    tasks = []
    for step in plan['steps']:
        # Index-only differences are deferred to the exact final synchronization.
        if not step['files']:
            continue
        entries = step['after'] if step['operation'] == 'UPDATE_CATALOG' else [step['after']]
        before = step['before'] if step['operation'] == 'UPDATE_CATALOG' else [step['before']]
        names = list(dict.fromkeys(e['name'] for e in entries + before if e))
        cases = []
        for name in names:
            objects = []
            for item in step['files']:
                if item['key'].split('/', 1)[0] != name:
                    continue
                obj = {'local_path': item['path'], 'target_key': item['key'],
                       'bytes': item['bytes'], 'sha256': item['sha256'],
                       'content_type': content_type(item['key']), 'action': 'upload',
                       'reason': item.get('reasons', ['selected package directory replacement'])}
                previous = old.get(name, {}).get(item.get('kind'), {})
                if previous and previous['file_name'] != item['key'].split('/', 1)[1]:
                    obj['old_key'] = name + '/' + previous['file_name']
                objects.append(obj)
            package = next((p for p in step['packages'] if p['name'] == name), None)
            directory = None
            if package:
                prefix = name + '/extracted/'
                expanded = [o for o in objects if o['target_key'].startswith(prefix)]
                local_root = None
                if expanded:
                    first = expanded[0]
                    local_root = str(Path(first['local_path']).parents[
                        len(Path(first['target_key'][len(prefix):]).parts) - 1])
                directory = {'local_root': local_root, 'target_prefix': prefix, 'objects': expanded}
            cases.append({'name': name, 'objects': objects, 'directory': directory})
        tasks.append({'id': step['id'], 'operation': step['operation'], 'name': step['name'],
                      'before': step['before'], 'after': step['after'], 'cases': cases})
    return tasks


class Reporter:
    """Durable statuses distinguish a committed manifest from a failed CDN refresh."""
    def __init__(self, plan, output):
        self.output = output
        self.started = time.monotonic()
        self.state = {'after': plan['after'], 'steps': [
            {'id': s['id'], 'operation': s['operation'], 'name': s['name'], 'status': 'waiting',
             'manifest_committed': False, 'manifest_verified': False, 'cdn': 'waiting',
             'files': [{'key': f['key'], 'status': 'waiting'} for f in s['files']]}
            for s in plan['steps']]}
        atomic_json(output / 'status.json', self.state)
        self.events = queue.Queue(maxsize=1024)
        self.writer_error = None
        self.object_results = {}
        self.uploaded_keys = set()
        self.writer = threading.Thread(target=self._write_events, name='publication-events')
        self.writer.start()

    def _write_events(self):
        try:
            with (self.output / 'events.jsonl').open('w', encoding='utf-8') as stream:
                while True:
                    event = self.events.get()
                    if event is None:
                        break
                    stream.write(json.dumps(event, ensure_ascii=True) + '\n')
                    stream.flush()
                    if event['phase'] == 'file':
                        # Later HEAD or repair failures cannot undo a successful write.
                        if event['status'] == 'completed':
                            self.uploaded_keys.add(event['target_key'])
                        self.object_results[event['target_key']] = ('uploaded' if event['status'] == 'completed' else 'failed')
                    elif event['phase'] == 'head':
                        self.object_results[event['key']] = ('completed' if event['status'] == 'completed' else event['status'])
        except BaseException as error:
            self.writer_error = error

    def save(self):
        atomic_json(self.output / 'status.json', self.state)

    def close(self):
        self._enqueue(None)
        self.writer.join()
        if self.writer_error:
            raise self.writer_error
        for step in self.state['steps']:
            for item in step['files']:
                if item['key'] in self.object_results:
                    item['status'] = self.object_results[item['key']]
        self.state['uploaded_uncommitted'] = [item['key'] for step in self.state['steps']
            if step['manifest_committed'] is not True for item in step['files']
            if item['key'] in self.uploaded_keys]
        self.save()

    def _enqueue(self, value):
        while True:
            if self.writer_error:
                raise self.writer_error
            try:
                self.events.put(value, timeout=0.1)
                return
            except queue.Full:
                pass

    def event(self, step, phase, status, **details):
        record = {'time': datetime.now(timezone.utc).isoformat(), 'step': step['id'],
                  'phase': phase, 'status': status, **details}
        self._enqueue(record)
        if phase not in ('file', 'head', 'replace', 'cleanup_object'):
            print(json.dumps(record, ensure_ascii=True), flush=True)


    def summary(self):
        rows = ['### Case publication', '', '| Step | Operation | Case | Status | Manifest accepted | Read-back verified | CDN |',
                '| --- | --- | --- | --- | --- | --- | --- |']
        for s in self.state['steps']:
            rows.append('| {id} | {operation} | {name} | {status} | {manifest_committed} | {manifest_verified} | {cdn} |'.format(**s))
        rows.extend(['', 'Elapsed: %.1fs' % (time.monotonic() - self.started), ''])
        text = '\n'.join(rows)
        (self.output / 'summary.md').write_text(text, encoding='utf-8')
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
                stream.write(text)
