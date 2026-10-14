"""Publication contract: selected writes, task batching and shared bounded work."""
import copy
import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_publish import GitFixture, publisher
from test_resource_sources import Store


class ConcurrentPublicationTests(GitFixture):
    def test_selected_cover_overwrites_even_when_target_metadata_matches_without_preflight_head(self):
        self.change()
        plan = self.plan()
        store = Store(plan['baseline'])
        key = 'a-case/A Case.png'
        store.objects[key] = {'ContentLength': 8, 'Metadata': {'sha256': self.entry['cover']['sha256']}}
        staged = publisher.prepare_resources(self.root, plan, store, self.root / 'sources')
        self.assertEqual(store.heads, [])
        self.assertIn(key, [f['key'] for f in staged['files']])
        publisher.apply(self.root, staged, store)
        self.assertEqual(store.writes.count(key), 1)
        self.assertEqual(store.heads, [key])

    def test_two_tasks_and_tail_commit_twice_without_duplicate_final_write(self):
        self.add_case('B Case', 'b-case'); self.add_case('C Case', 'c-case')
        self.write_manifest(); baseline_commit = self.commit()
        for entry in self.entries:
            self.entry = entry
            self.change()
        plan = self.ready(self.plan(before=baseline_commit))
        store = Store(plan['baseline'])
        manifests = []
        original = store.commit_manifest
        def commit(path):
            manifests.append(json.loads(path.read_text()))
            original(path)
        store.commit_manifest = commit
        publisher.apply(self.root, plan, store)
        self.assertEqual(len(manifests), 2)
        self.assertEqual(manifests[0][:2], self.entries[:2])
        self.assertNotEqual(manifests[0][2], self.entries[2])
        self.assertEqual(store.manifest, (self.root / 'manifest.json').read_text())

    def test_rename_records_exact_old_key_and_deletes_after_readback(self):
        cover = self.entry['cover']
        target = self.root / 'usecases/A Case/new.png'
        (self.root / cover['path']).rename(target)
        cover.update(path='usecases/A Case/new.png', file_name='new.png')
        self.write_manifest(); self.commit()
        plan = self.ready(self.plan())
        self.assertEqual(plan['tasks'][0]['cases'][0]['objects'][0]['old_key'], 'a-case/A Case.png')
        store = Store(plan['baseline'])
        deletes = []
        def delete(keys):
            self.assertEqual(json.loads(store.manifest), self.entries)
            deletes.extend(keys)
        store.delete_keys = delete
        publisher.apply(self.root, plan, store)
        self.assertEqual(deletes, ['a-case/A Case.png'])

    def test_console_output_does_not_include_per_object_events(self):
        self.change()
        plan = self.ready(self.plan())
        stream = io.StringIO()
        with redirect_stdout(stream):
            publisher.apply(self.root, plan, Store(plan['baseline']))
        self.assertNotIn('Candidate:', stream.getvalue())
        self.assertNotIn('"phase": "file"', stream.getvalue())
        self.assertNotIn('Before:', stream.getvalue())

    def test_catalog_rename_without_collision_is_one_task(self):
        self.add_case('B Case', 'b-case')
        old = copy.deepcopy(self.entries)
        for entry in old:
            entry.update(catalog='Old', catalog_name='old')
            for kind in publisher.KINDS:
                entry[kind]['path'] = 'Old/' + entry['name'] + '/' + entry[kind]['file_name']
        for entry in self.entries:
            entry.update(catalog='New', catalog_name='new')
            for kind in publisher.KINDS:
                resource = entry[kind]
                target = self.root / 'New' / entry['name'] / resource['file_name']
                target.parent.mkdir(parents=True, exist_ok=True)
                (self.root / resource['path']).rename(target)
                resource['path'] = target.relative_to(self.root).as_posix()
            self.entry = entry
            self.change()
        plan = self.ready(publisher.prepare(self.root, json.dumps(old), self.git('rev-parse', 'HEAD')))
        self.assertEqual(len(plan['tasks']), 1)
        self.assertEqual(len(plan['tasks'][0]['cases']), 2)

    def sdk_store(self, plan, env=None):
        from fake_sdk import FakeS3Client
        client = FakeS3Client(self.root / 's3', env or {})
        path = self.root / 's3/bucket/cases/manifest.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plan['baseline'])
        return publisher.S3('s3://bucket/cases', client=client), client

    def test_head_repairs_only_bad_object_and_preserves_good_object(self):
        self.change(); self.change('introduction')
        plan = self.ready(self.plan())
        store, client = self.sdk_store(plan)
        original = client.head_object
        calls = []
        def head(**kwargs):
            calls.append(kwargs['Key'])
            result = original(**kwargs)
            if kwargs['Key'].endswith('.png') and calls.count(kwargs['Key']) == 1:
                result['Metadata'] = {}
            return result
        client.head_object = head
        with patch.dict('os.environ', {'AWS_DISTRIBUITION_ID': 'fixture'}), patch.object(store, 'refresh_paths'):
            publisher.apply(self.root, plan, store)
        uploads = [a['Key'] for op, a in client.sdk_calls if op == 'upload_file']
        self.assertEqual(uploads.count('cases/a-case/A Case.png'), 2)
        self.assertEqual(uploads.count('cases/a-case/A Case.md'), 1)
        events = [json.loads(line) for line in (self.root / '.publication/events.jsonl').read_text().splitlines()]
        self.assertEqual(sum(e.get('transfer_action') == 'replace' for e in events), 1)

    def test_head_missing_repairs_and_exhaustion_blocks_commit(self):
        self.change(); plan = self.ready(self.plan())
        store, client = self.sdk_store(plan, {'FAKE_S3_BAD_HEAD': '1'})
        with self.assertRaisesRegex(ValueError, '2 repair rounds'):
            publisher.apply(self.root, plan, store)
        self.assertEqual(sum(op == 'upload_file' for op, _ in client.sdk_calls), 3)
        self.assertFalse(any(op == 'put_object' for op, _ in client.sdk_calls))
        client.env = {}
        original = client.head_object
        missing = [True]
        def head(**kwargs):
            if missing.pop() if missing else False:
                client.fail('404', 'HeadObject')
            return original(**kwargs)
        client.head_object = head
        with patch.object(store, 'refresh_paths'):
            publisher.apply(self.root, plan, store)
        self.assertEqual(sum(op == 'upload_file' for op, _ in client.sdk_calls), 5)

    def test_permission_transient_and_timeout_errors_never_become_repairs(self):
        self.change(); plan = self.ready(self.plan())
        from botocore.exceptions import ClientError, ReadTimeoutError
        for code in ('403', '503', 'timeout'):
            store, client = self.sdk_store(plan, {'FAKE_S3_HEAD_ERROR': code})
            with self.subTest(code=code), self.assertRaises((ClientError, ReadTimeoutError)):
                publisher.apply(self.root, plan, store)
            self.assertEqual(sum(op == 'upload_file' for op, _ in client.sdk_calls), 1)
            self.assertFalse(any(op == 'put_object' for op, _ in client.sdk_calls))

    def test_cleanup_paginates_and_batches_1000_and_checks_partial_errors(self):
        self.change(); plan = self.ready(self.plan())
        store, client = self.sdk_store(plan)
        for number in range(1003):
            path = self.root / 's3/bucket/cases/a-case/extracted' / str(number)
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'old')
        sibling = self.root / 's3/bucket/cases/a-case/extracted-other/keep'
        sibling.parent.mkdir(); sibling.write_bytes(b'keep')
        keys = list(store.list_keys('a-case/extracted/'))
        self.assertEqual(len(keys), 1003)
        store.delete_keys(keys)
        self.assertEqual([len(a['Delete']['Objects']) for op, a in client.sdk_calls if op == 'delete_objects'], [1000, 3])
        self.assertTrue(sibling.exists())
        client.env = {'FAKE_S3_DELETE_ERROR': '1'}
        with self.assertRaisesRegex(ValueError, 'deletion failed'):
            store.delete_keys(['a-case/extracted/absent'])

    def test_recovery_retries_postprocess_without_upload_and_protects_referenced_key(self):
        from publication_runtime import recover
        cover = self.entry['cover']; old_key = 'a-case/' + cover['file_name']
        (self.root / cover['path']).rename(self.root / 'usecases/A Case/new.png')
        cover.update(path='usecases/A Case/new.png', file_name='new.png')
        self.write_manifest(); self.commit(); plan = self.ready(self.plan())
        store, client = self.sdk_store(plan, {'FAKE_S3_DELETE_ERROR': '1'})
        old_path = self.root / 's3/bucket/cases' / old_key
        old_path.parent.mkdir(parents=True, exist_ok=True); old_path.write_bytes(b'keep')
        with patch.object(store, 'refresh_paths'), self.assertRaisesRegex(ValueError, 'deletion failed'):
            publisher.apply(self.root, plan, store)
        batch_file = self.root / '.publication/batches/001/status.json'
        batch = json.loads(batch_file.read_text())
        self.assertTrue(batch['manifest_verified'])
        self.assertEqual(batch['cleanup_pending'], [old_key])
        self.assertTrue(batch['cdn_pending'])
        # A newer publisher reintroduced the old key; recovery must retain it.
        (self.root / 's3/bucket/cases/manifest.json').write_text(plan['baseline'])
        client.env = {}
        before = len(client.sdk_calls)
        with patch.object(store, 'refresh_paths') as refresh:
            recover(store, self.root / '.publication', root=self.root)
            refresh.assert_called_once_with(['/cases/manifest.json', '/cases/a-case/*'])
        self.assertTrue(old_path.exists())
        self.assertFalse(any(op in ('upload_file', 'put_object', 'delete_objects') for op, _ in client.sdk_calls[before:]))
        self.assertEqual(json.loads(batch_file.read_text())['cdn_pending'], [])

    def test_four_ordinary_tasks_overlap_and_ordered_batches_ignore_completion_order(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        import publication_runtime as runtime
        for letter in 'BCDEF':
            self.add_case(letter + ' Case', letter.lower() + '-case')
        self.write_manifest(); baseline = self.commit()
        for entry in self.entries:
            self.entry = entry; self.change()
        plan = self.ready(self.plan(before=baseline))
        store = Store(plan['baseline'])
        entered = {t['id']: threading.Event() for t in plan['tasks']}
        release = {t['id']: threading.Event() for t in plan['tasks']}
        exited = {t['id']: threading.Event() for t in plan['tasks']}
        lock = threading.Lock(); active = 0; maximum = 0
        manifests = []
        original_commit = store.commit_manifest
        def commit(path):
            manifests.append(json.loads(path.read_text())); original_commit(path)
        store.commit_manifest = commit
        def case(root, case, store, uploads, heads, stopped, reporter, task, workers):
            nonlocal active, maximum
            with lock:
                active += 1; maximum = max(maximum, active)
            entered[task['id']].set()
            if not release[task['id']].wait(10):
                raise AssertionError('Test release missing')
            with lock:
                active -= 1
            exited[task['id']].set()
            return {'success': 1}
        with patch.object(runtime, 'publish_case', side_effect=case), ThreadPoolExecutor(max_workers=1) as runner:
            future = runner.submit(publisher.apply, self.root, plan, store)
            try:
                for number in range(1, 5):
                    self.assertTrue(entered[number].wait(10))
                self.assertFalse(entered[5].is_set())
                for number in (4, 3, 2):
                    release[number].set(); self.assertTrue(exited[number].wait(10))
                self.assertEqual(manifests, [])
            finally:
                for event in release.values():
                    event.set()
            future.result(timeout=10)
        self.assertEqual(maximum, 4)
        self.assertEqual(len(manifests), 3)
        self.assertEqual(manifests[0][:2], self.entries[:2])
        self.assertNotEqual(manifests[0][2], self.entries[2])
        self.assertEqual(manifests[-1], self.entries)

    def test_shared_pools_cap_uploads_and_heads_without_nested_threads(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        import publication_runtime as runtime
        from publication_steps import Reporter
        # Exercise real case worker and object scheduling, with two simultaneous cases.
        self.change(); plan = self.ready(self.plan())
        original = plan['tasks'][0]['cases'][0]['objects'][0]
        cases = [{'name': name, 'directory': None, 'objects': [
            {**original, 'target_key': name + '/' + str(i)} for i in range(40)]}
            for name in ('a-case', 'b-case')]
        lock = threading.Lock(); active = {'upload': 0, 'head': 0}; maximum = dict(active)
        reached = {phase: threading.Event() for phase in active}
        release = {phase: threading.Event() for phase in active}
        limits = {'upload': 16, 'head': 32}
        class BlockingStore(Store):
            def block(inner, phase):
                with lock:
                    active[phase] += 1; maximum[phase] = max(maximum[phase], active[phase])
                    if active[phase] == limits[phase]: reached[phase].set()
                if not release[phase].wait(10): raise AssertionError('Pool did not overlap')
                with lock: active[phase] -= 1
            def upload(inner, path, key):
                inner.block('upload'); super().upload(path, key)
            def head(inner, key):
                inner.block('head'); return super().head(key)
        store = BlockingStore()
        output = self.root / 'publication'; output.mkdir()
        reporter = Reporter(plan, output)
        try:
            with ThreadPoolExecutor(max_workers=16) as uploads, ThreadPoolExecutor(max_workers=32) as heads, ThreadPoolExecutor(max_workers=2) as runners:
                futures = [runners.submit(runtime.publish_case, self.root, case, store, uploads, heads,
                                          threading.Event(), reporter, plan['tasks'][0], 16) for case in cases]
                try:
                    self.assertTrue(reached['upload'].wait(10)); release['upload'].set()
                    self.assertTrue(reached['head'].wait(10))
                finally:
                    for event in release.values(): event.set()
                for future in futures: future.result(timeout=10)
        finally:
            reporter.close()
        self.assertEqual(maximum, {'upload': 16, 'head': 32})
        self.assertEqual(len(store.heads), 80)

    def test_independent_additions_in_same_catalog_remain_separate_tasks(self):
        for letter in 'BC':
            entry = self.add_case(letter + ' Case', letter.lower() + '-case')
            entry.update(catalog='Science', catalog_name='science')
            for kind in publisher.KINDS:
                resource = entry[kind]
                target = self.root / 'Science' / entry['name'] / resource['file_name']
                target.parent.mkdir(parents=True, exist_ok=True)
                (self.root / resource['path']).rename(target)
                resource['path'] = target.relative_to(self.root).as_posix()
        self.write_manifest(); self.commit()
        plan = self.plan()
        self.assertEqual(len(plan['tasks']), 2)
        self.assertEqual([len(t['cases']) for t in plan['tasks']], [1, 1])

    def test_multicase_exclusive_barrier_prevents_later_tasks_overtaking(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        import publication_runtime as runtime
        for letter in 'BCDEFGHI':
            self.add_case(letter + ' Case', letter.lower() + '-case')
        old = copy.deepcopy(self.entries)
        for previous, entry in zip(old[1:7], self.entries[1:7]):
            previous.update(catalog='Old', catalog_name='old')
            entry.update(catalog='New', catalog_name='new')
            for kind in publisher.KINDS:
                resource = entry[kind]
                previous[kind]['path'] = 'Old/' + entry['name'] + '/' + resource['file_name']
                target = self.root / 'New' / entry['name'] / resource['file_name']
                target.parent.mkdir(parents=True, exist_ok=True)
                (self.root / resource['path']).rename(target)
                resource['path'] = target.relative_to(self.root).as_posix()
        for entry in self.entries:
            self.entry = entry; self.change()
        plan = self.ready(publisher.prepare(self.root, json.dumps(old), self.git('rev-parse', 'HEAD')))
        self.assertEqual([len(t['cases']) for t in plan['tasks']], [1, 6, 1, 1])
        entered = {e['name']: threading.Event() for e in self.entries}
        release_a = threading.Event(); release_group = threading.Event()
        active = set(); lock = threading.Lock(); maximum = 0
        def case(root, case, store, uploads, heads, stopped, reporter, task, workers):
            nonlocal maximum
            name = case['name']
            with lock:
                active.add(name); maximum = max(maximum, len(active))
                if name in ('h-case', 'i-case'):
                    self.assertFalse(active & {'b-case', 'c-case', 'd-case', 'e-case', 'f-case', 'g-case'})
                if name in ('b-case', 'c-case', 'd-case', 'e-case', 'f-case', 'g-case'):
                    self.assertNotIn('a-case', active)
            entered[name].set()
            if name == 'a-case':
                self.assertTrue(release_a.wait(10))
            elif name in ('b-case', 'c-case', 'd-case', 'e-case', 'f-case', 'g-case'):
                self.assertTrue(release_group.wait(10))
            with lock: active.remove(name)
            return {'success': 1}
        with patch.object(runtime, 'publish_case', side_effect=case), ThreadPoolExecutor(max_workers=1) as runner:
            future = runner.submit(publisher.apply, self.root, plan, Store(plan['baseline']))
            try:
                self.assertTrue(entered['a-case'].wait(10))
                self.assertFalse(entered['b-case'].is_set())
                self.assertFalse(entered['f-case'].is_set())
                release_a.set()
                for name in ('b-case', 'c-case', 'd-case', 'e-case'):
                    self.assertTrue(entered[name].wait(10))
                self.assertFalse(entered['f-case'].is_set())
                self.assertFalse(entered['g-case'].is_set())
                self.assertFalse(entered['h-case'].is_set())
            finally:
                release_a.set(); release_group.set()
            future.result(timeout=10)
        self.assertEqual(maximum, 4)

    def test_failure_from_later_task_prevents_ready_batch_and_new_dispatch(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        import publication_runtime as runtime
        for letter in 'BCDE': self.add_case(letter + ' Case', letter.lower() + '-case')
        self.write_manifest(); base = self.commit()
        for entry in self.entries: self.entry = entry; self.change()
        plan = self.ready(self.plan(before=base)); store = Store(plan['baseline'])
        barrier = threading.Barrier(4)
        entered = []
        def case(root, case, store, uploads, heads, stopped, reporter, task, workers):
            entered.append(case['name']); barrier.wait(timeout=10)
            if case['name'] == 'd-case':
                raise ValueError('injected later task failure')
            self.assertTrue(stopped.wait(10))
            return {'success': 1}
        with patch.object(runtime, 'publish_case', side_effect=case), self.assertRaises(Exception):
            publisher.apply(self.root, plan, store)
        self.assertEqual(store.manifest, plan['baseline'])
        self.assertNotIn('e-case', entered)
        self.assertEqual(store.writes, [])

    def test_recovery_rejects_forged_cleanup_and_case_paths(self):
        from publication_runtime import recover
        self.change(); plan = self.ready(self.plan())
        store, client = self.sdk_store(plan)
        with patch.object(store, 'refresh_paths', side_effect=OSError('CDN offline')), self.assertRaises(OSError):
            publisher.apply(self.root, plan, store)
        path = self.root / '.publication/batches/001/status.json'
        data = json.loads(path.read_text())
        data['tasks'][0]['before']['cover']['file_name'] = 'extracted/session.json'
        data['cleanup_pending'] = ['a-case/extracted/session.json']
        path.write_text(json.dumps(data))
        before = len(client.sdk_calls)
        with self.assertRaises(ValueError):
            recover(store, self.root / '.publication', root=self.root)
        self.assertFalse(any(op == 'delete_objects' for op, _ in client.sdk_calls[before:]))

    def test_repair_rechecks_staged_bytes_before_reupload(self):
        self.change(); plan = self.ready(self.plan())
        store = Store(plan['baseline'])
        def corrupt(key):
            Path(plan['files'][0]['path']).write_bytes(b'tampered')
            return None
        store.head = corrupt
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            publisher.apply(self.root, plan, store)
        self.assertEqual(store.writes, ['a-case/A Case.png'])

    def test_log_lines_do_not_scale_with_object_count(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from publication_steps import Reporter
        from publication_runtime import publish_case
        self.change(); plan = self.ready(self.plan())
        obj = plan['tasks'][0]['cases'][0]['objects'][0]
        counts = []
        for size in (1, 100):
            output = self.root / ('publication-' + str(size)); output.mkdir()
            reporter = Reporter(plan, output); console = io.StringIO()
            try:
                with redirect_stdout(console), ThreadPoolExecutor(max_workers=16) as uploads, ThreadPoolExecutor(max_workers=32) as heads:
                    publish_case(self.root, {'name': 'a-case', 'directory': None, 'objects': [
                        {**obj, 'target_key': 'a-case/' + str(i)} for i in range(size)]},
                        Store(), uploads, heads, threading.Event(), reporter, plan['tasks'][0], 16)
            finally:
                reporter.close()
            counts.append(len(console.getvalue().splitlines()))
            self.assertGreaterEqual(len((output / 'events.jsonl').read_text().splitlines()), size * 2)
        self.assertEqual(counts, [2, 2])

    def test_invalid_upload_workers_exit_without_leaking_reporter(self):
        import subprocess
        import sys
        import os
        from test_publish import SCRIPTS
        self.change(); plan = self.ready(self.plan())
        self.sdk_store(plan)
        path = self.root / 'plan.json'; path.write_text(json.dumps(plan))
        env = {**os.environ, 'AWS_TARGET_FOLDER': 's3://bucket/cases', 'AWS_DISTRIBUITION_ID': 'fixture',
               'FAKE_S3_ROOT': str(self.root / 's3'), 'PUBLICATION_UPLOAD_WORKERS': 'abc',
               'PYTHONPATH': str(Path(__file__).parent / 'sdk_boundary') + os.pathsep + str(Path(__file__).parent)}
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'publish.py'), '--apply-plan', str(path)],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 's3/calls.jsonl').exists())

    def test_sdk_retries_503_on_local_http_and_uses_configured_resource_client(self):
        import os
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from botocore.exceptions import ClientError
        class Handler(BaseHTTPRequestHandler):
            calls = 0
            def do_HEAD(inner):
                Handler.calls += 1
                inner.send_response(503); inner.send_header('Content-Length', '0'); inner.end_headers()
            def log_message(inner, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        try:
            with patch.dict(os.environ, {'AWS_ACCESS_KEY_ID': 'fixture', 'AWS_SECRET_ACCESS_KEY': 'fixture',
                    'AWS_SESSION_TOKEN': '', 'AWS_DEFAULT_REGION': 'us-east-1', 'AWS_EC2_METADATA_DISABLED': 'true',
                    'AWS_ENDPOINT_URL_S3': 'http://127.0.0.1:' + str(server.server_port)}):
                store = publisher.S3('s3://bucket/cases')
                self.assertEqual(store.client.meta.config.max_pool_connections, 64)
                with patch('botocore.endpoint.time.sleep'), self.assertRaises(ClientError) as caught:
                    store.head('a-case/cover.png')
                self.assertEqual(caught.exception.response['Error']['Code'], '503')
                self.assertEqual(Handler.calls, 4)
                self.assertFalse(store.transfer.use_threads)
                store.client.close()
                store.manifest_client.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_sdk_head_datetime_fields_do_not_break_evidence(self):
        from datetime import datetime, timezone
        self.change(); plan = self.ready(self.plan())
        store = Store(plan['baseline'])
        original = store.head
        def head(key):
            return {**original(key), 'LastModified': datetime.now(timezone.utc),
                    'ETag': '"fixture"', 'ResponseMetadata': {'HTTPStatusCode': 200}}
        store.head = head
        publisher.apply(self.root, plan, store)
        self.assertEqual(store.manifest, (self.root / 'manifest.json').read_text())
        events = [json.loads(line) for line in (self.root / '.publication/events.jsonl').read_text().splitlines()]
        observed = next(e['actual'] for e in events if e['phase'] == 'head')
        self.assertEqual(observed, {'ContentLength': 8, 'Metadata': {'sha256': self.entry['cover']['sha256']}})

    def test_reused_output_archives_old_batches_before_smaller_run_recovery(self):
        from publication_runtime import recover
        self.add_case('B Case', 'b-case'); self.add_case('C Case', 'c-case')
        self.write_manifest(); base = self.commit()
        for entry in self.entries: self.entry = entry; self.change()
        plan = self.ready(self.plan(before=base)); store, client = self.sdk_store(plan)
        output = self.root / '.publication'
        with patch.object(store, 'refresh_paths'):
            publisher.apply(self.root, plan, store)
        self.assertTrue((output / 'batches/002/status.json').exists())
        baseline = store.read_manifest()
        self.change('introduction')
        plan = self.ready(publisher.prepare(self.root, baseline, self.git('rev-parse', 'HEAD')))
        with patch.object(store, 'refresh_paths', side_effect=OSError('CDN offline')), self.assertRaises(OSError):
            publisher.apply(self.root, plan, store)
        self.assertFalse((output / 'batches/002').exists())
        archives = list(self.root.glob('.publication-previous-*'))
        self.assertEqual(len(archives), 1)
        self.assertTrue((archives[0] / 'batches/002/status.json').is_file())
        with patch.object(store, 'refresh_paths'):
            recover(store, output, root=self.root)
        self.assertEqual(json.loads((output / 'batches/001/status.json').read_text())['cdn_pending'], [])
