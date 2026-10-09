"""Download real tar.gz fixtures over HTTP; publish through a filesystem AWS boundary."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import re
import shlex
from pathlib import Path
import subprocess
import sys
import tarfile
import threading

from test_publish import GitFixture, SCRIPTS, publisher


def package_bytes(storage_key='artifacts/project/session/.provenance/version/content',
                  missing=False, unsafe_member=None, invalid_session=False):
    session = {'version': 2, 'session': {
        'cwd': '$DATA/workspaces/history',
        'artifacts': [{'path': '$DATA/' + storage_key, 'name': 'plot.svg'}],
        'messages': [{'content': 'Keep historical text: $DATA/artifacts/old'}]}}
    assets = [(storage_key, b'<svg>fixture</svg>'), ('uploads/project/data.csv', b'x,y\n1,2'),
              ('notebooks/project/run.json', b'{}')]
    inventory = []
    files = {}
    for index, (key, data) in enumerate(assets):
        path = 'objects/object-' + str(index)
        inventory.append({'path': path, 'storageKey': key, 'kind': 'file',
                          'sizeBytes': len(data), 'checksum': hashlib.sha256(data).hexdigest()})
        if not (missing and index == 0):
            files[path] = data
    files['session.json'] = b'broken' if invalid_session else json.dumps(session).encode()
    inventory.append({'path': 'session.json', 'kind': 'session'})
    files['manifest.json'] = json.dumps({'format': 'open-science-session', 'schemaVersion': 1,
                                        'inventory': inventory}).encode()
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w:gz') as archive:
        # Metadata need not precede object members in an archive.
        for name, data in files.items():
            item = tarfile.TarInfo(name); item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
        if unsafe_member:
            item = tarfile.TarInfo(unsafe_member)
            item.type = tarfile.SYMTYPE; item.linkname = '/tmp/outside'
            archive.addfile(item)
    return result.getvalue()


class DownloadHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.server.head_requests.append(self.path)
        data = self.server.responses.get(self.path)
        if data is None:
            self.send_error(404); return
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()

    def do_GET(self):
        self.server.requests.append(self.path)
        data = self.server.responses.get(self.path)
        if data is None:
            self.send_error(404); return
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def log_message(self, *args):
        pass


class ExtractionTests(GitFixture):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), DownloadHandler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:' + str(cls.http.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join()

    def setUp(self):
        super().setUp()
        self.http.requests = []; self.http.responses = {}; self.http.head_requests = []
        self.output = self.root / 'staging'
        self.plan_file = self.root / 'publication-plan.json'

    def declare(self, data=None, remote=False):
        data = data if data is not None else package_bytes()
        resource = self.entry['case']
        resource['sha256'] = hashlib.sha256(data).hexdigest()
        resource['bytes'] = len(data)
        resource['release_url'] = self.url + '/release.science' if remote else ''
        if remote:
            (self.root / resource['path']).unlink(missing_ok=True)
        else:
            (self.root / resource['path']).write_bytes(data)
        self.write_manifest(); after = self.commit()
        self.base_url = self.url + '/repository/' + after
        self.http.responses = {
            '/release.science': data,
            '/repository/' + after + '/A%20Case/A%20Case.science': data}
        for kind in ('cover', 'introduction'):
            if kind in self.entry:
                resource = self.entry[kind]
                from urllib.parse import quote
                self.http.responses['/repository/' + after + '/' + quote(resource['path'])] = (
                    self.root / resource['path']).read_bytes()
        self.plan_file.write_text(json.dumps(self.plan()))
        return after

    def extract(self, expected=0, attempt='1'):
        result = subprocess.run(
            [sys.executable, '-B', str(SCRIPTS / 'extract_science.py'),
             '--plan', str(self.plan_file), '--output', str(self.output)],
            cwd=self.root, env={**os.environ, 'CASE_FILE_BASE_URL': self.base_url,
                                'GITHUB_RUN_ATTEMPT': attempt}, text=True, capture_output=True)
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def s3_fixture(self):
        fake = self.root / 'bin'; fake.mkdir()
        aws = fake / 'aws'
        aws.write_text('#!' + sys.executable + '\n' + Path(__file__).with_name('fake_s3.py').read_text())
        aws.chmod(0o755)
        s3 = self.root / 's3'; s3.mkdir()
        for name in ['a-case/extracted/stale.txt', 'a-case/extracted/.hidden/old',
                     'a-case/extracted-other/keep.txt', 'other/extracted/keep.txt', 'a-case/cover.png']:
            path = s3 / 'bucket/cases' / name
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'old')
        env = {**os.environ, 'PATH': str(fake) + os.pathsep + os.environ['PATH'],
               'FAKE_S3_ROOT': str(s3), 'AWS_TARGET_FOLDER': 's3://bucket/cases'}
        return s3, env

    def publish(self, env):
        return subprocess.run(
            [sys.executable, '-B', str(SCRIPTS / 'publish.py'), '--apply-plan', str(self.plan_file)],
            cwd=self.root, env=env, text=True, capture_output=True)

    def test_repository_package_is_downloaded_and_old_local_output_is_removed(self):
        after = self.declare()
        # Checkout bytes must not be used as the unpacking source.
        (self.root / self.entry['case']['path']).write_bytes(b'not the download')
        old = self.output / 'a-case/extracted/stale.txt'
        old.parent.mkdir(parents=True); old.write_text('stale')
        self.extract()
        self.assertEqual(self.http.requests, ['/repository/' + after + '/A%20Case/A%20Case.science'])
        self.assertFalse(old.exists())
        directory = old.parent
        self.assertEqual((directory / 'artifacts/project/session/.provenance/version/content').read_bytes(),
                         b'<svg>fixture</svg>')
        self.assertEqual((directory / 'uploads/project/data.csv').read_bytes(), b'x,y\n1,2')
        session = json.loads((directory / 'session.json').read_text())['session']
        self.assertEqual(session['artifacts'][0]['path'], 'artifacts/project/session/.provenance/version/content')
        self.assertEqual(session['cwd'], '$DATA/workspaces/history')
        self.assertEqual(session['messages'][0]['content'], 'Keep historical text: $DATA/artifacts/old')

    def test_remote_package_download_and_s3_replacement_preserve_unrelated_objects(self):
        self.declare(remote=True); self.extract()
        self.assertEqual(self.http.requests, ['/release.science'])
        s3, env = self.s3_fixture()
        result = self.publish(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        target = s3 / 'bucket/cases'
        self.assertFalse((target / 'a-case/extracted/stale.txt').exists())
        self.assertFalse((target / 'a-case/extracted/.hidden/old').exists())
        for name in ['a-case/extracted-other/keep.txt', 'other/extracted/keep.txt', 'a-case/cover.png']:
            self.assertEqual((target / name).read_bytes(), b'old')
        self.assertEqual((target / 'a-case/extracted/uploads/project/data.csv').read_bytes(), b'x,y\n1,2')
        calls = [json.loads(line) for line in (s3 / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual(calls[0], ['s3', 'rm', 's3://bucket/cases/a-case/extracted/',
                                   '--recursive', '--only-show-errors'])
        self.assertEqual(calls[-1][3], 's3://bucket/cases/manifest.json')
        metadata = json.loads((s3 / 'metadata.json').read_text())
        for path in (target / 'a-case/extracted').rglob('*'):
            if path.is_file():
                key = 'bucket/cases/' + path.relative_to(target).as_posix()
                self.assertEqual(metadata[key]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_bad_download_hash_keeps_plan_unready_and_prevents_s3_changes(self):
        self.declare(remote=True)
        self.http.responses['/release.science'] = package_bytes(storage_key='uploads/changed')
        result = self.extract(expected=1)
        self.assertIn('SHA-256', result.stderr)
        s3, env = self.s3_fixture()
        result = self.publish(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((s3 / 'calls.jsonl').exists())

    def test_missing_invalid_or_unsafe_archive_fails_before_publishing(self):
        for data in [package_bytes(missing=True), package_bytes(invalid_session=True),
                     package_bytes(storage_key='../escape'),
                     package_bytes(storage_key='/absolute'),
                     package_bytes(unsafe_member='objects/link')]:
            with self.subTest(digest=hashlib.sha256(data).hexdigest()):
                self.declare(data, remote=True)
                original = self.plan_file.read_bytes()
                self.extract(expected=1)
                self.assertEqual(self.plan_file.read_bytes(), original)
                self.assertFalse((self.output / 'escape').exists())

    def test_empty_package_plan_does_not_download_or_clear_anything(self):
        self.base_url = self.url
        self.plan_file.write_text(json.dumps(self.plan()))
        self.extract()
        self.assertEqual(self.http.requests, [])
        self.assertFalse(self.output.exists())

    def test_delete_or_upload_failure_never_publishes_manifest(self):
        self.declare(remote=True); self.extract()
        s3, env = self.s3_fixture()
        for operation in ['rm', 'cp']:
            with self.subTest(operation=operation):
                result = self.publish({**env, 'FAKE_S3_FAIL': operation})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((s3 / 'bucket/cases/manifest.json').exists())

    def test_later_package_failure_does_not_publish_an_earlier_success(self):
        self.declare(remote=True)
        other = self.add_case('B Case', 'b-case')
        broken = package_bytes(missing=True)
        other['case'].update(release_url=self.url + '/broken.science', bytes=len(broken),
                             sha256=hashlib.sha256(broken).hexdigest())
        (self.root / other['case']['path']).unlink()
        self.write_manifest(); self.commit()
        self.plan_file.write_text(json.dumps(self.plan()))
        self.http.responses['/broken.science'] = broken
        self.extract(expected=1)
        self.assertTrue((self.output / 'a-case/extracted/session.json').is_file())
        s3, env = self.s3_fixture()
        result = self.publish(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((s3 / 'calls.jsonl').exists())
        self.assertEqual((s3 / 'bucket/cases/a-case/extracted/stale.txt').read_bytes(), b'old')

    def test_missing_staged_file_blocks_deletion_even_with_ready_plan(self):
        self.declare(remote=True); self.extract()
        (self.output / 'a-case/extracted/uploads/project/data.csv').unlink()
        s3, env = self.s3_fixture()
        result = self.publish(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((s3 / 'calls.jsonl').exists())
        self.assertEqual((s3 / 'bucket/cases/a-case/extracted/stale.txt').read_bytes(), b'old')

    def test_rerun_and_checkout_mismatch_do_not_download(self):
        self.declare(remote=True)
        self.extract(expected=1, attempt='2')
        self.git('commit', '--allow-empty', '-qm', 'test: different target')
        self.extract(expected=1)
        self.assertEqual(self.http.requests, [])

    def pipeline(self, env):
        # Execute the actual four workflow commands under Actions' fail-fast shell.
        workflow = (SCRIPTS.parent / 'workflows/check-structure.yml').read_text().split('  publish:', 1)[1]
        commands = re.findall(r'^        run: (python3 .+)$', workflow, re.M)
        self.assertEqual(len(commands), 4)
        script = '\n'.join(commands).replace('python3 .github/scripts/',
                                               shlex.quote(sys.executable) + ' -B ' + str(SCRIPTS) + '/')
        return subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=self.root,
                              env={**env, 'TARGET_SHA': self.git('rev-parse', 'HEAD'),
                                   'RUNNER_TEMP': str(self.root), 'CASE_FILE_BASE_URL': self.base_url},
                              text=True, capture_output=True)

    def test_workflow_full_incremental_and_removal_publications(self):
        self.declare()
        s3, env = self.s3_fixture()
        result = self.pipeline(env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        target = s3 / 'bucket/cases'
        self.assertEqual((target / 'manifest.json').read_bytes(), (self.root / 'manifest.json').read_bytes())
        self.assertEqual((target / 'a-case/A Case.science').read_bytes(),
                         (self.root / self.entry['case']['path']).read_bytes())
        self.assertEqual(len(self.http.head_requests), 3)
        self.assertEqual(len(self.http.requests), 1)
        # The second run consults the newly published manifest and does no work.
        (s3 / 'calls.jsonl').write_text(''); self.http.requests.clear(); self.http.head_requests.clear()
        result = self.pipeline(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in (s3 / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual([call[:2] for call in calls], [['s3api', 'get-object']])
        self.assertEqual(self.http.requests + self.http.head_requests, [])
        # Removing the record only replaces the root index.
        self.entries = []; self.write_manifest(); self.commit()
        (s3 / 'calls.jsonl').write_text('')
        result = self.pipeline(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((target / 'manifest.json').read_text(), '[]')
        self.assertTrue((target / 'a-case/A Case.science').is_file())
        self.assertTrue((target / 'a-case/extracted/session.json').is_file())
        calls = [json.loads(line) for line in (s3 / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual([call[:2] for call in calls], [['s3api', 'get-object'], ['s3', 'cp']])

    def test_workflow_source_or_head_failure_prevents_download_and_s3_writes(self):
        self.declare(remote=True)
        s3, env = self.s3_fixture()
        local = self.root / self.entry['cover']['path']
        data = local.read_bytes(); local.unlink()
        result = self.pipeline(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing', result.stderr)
        local.write_bytes(data)
        self.http.responses['/release.science'] = b'wrong size'
        result = self.pipeline(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('HEAD reports', result.stderr)
        self.assertEqual(self.http.requests, [])
        calls = [json.loads(line) for line in (s3 / 'calls.jsonl').read_text().splitlines()]
        self.assertTrue(all(call[:2] == ['s3api', 'get-object'] for call in calls))

    def test_workflow_replaces_science_and_preserves_old_manifest_on_upload_failure(self):
        self.declare(remote=True)
        s3, env = self.s3_fixture()
        result = self.pipeline(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        target = s3 / 'bucket/cases'
        prior = (target / 'manifest.json').read_bytes()
        self.declare(package_bytes(storage_key='uploads/replacement'), remote=True)
        (s3 / 'calls.jsonl').write_text('')
        result = self.pipeline({**env, 'FAKE_S3_FAIL': 'cp'})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((target / 'manifest.json').read_bytes(), prior)
        calls = [json.loads(line) for line in (s3 / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual([call[:2] for call in calls],
                         [['s3api', 'get-object'], ['s3', 'rm'], ['s3', 'cp']])
        # A new run plans against the unchanged S3 index and retries replacement.
        result = self.pipeline(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((target / 'a-case/extracted/uploads/replacement').is_file())
        self.assertFalse((target / 'a-case/extracted/artifacts').exists())
        self.assertNotEqual((target / 'manifest.json').read_bytes(), prior)

    def test_workflow_invalid_or_unreadable_baseline_stops_before_processing(self):
        self.declare(remote=True)
        s3, env = self.s3_fixture()
        target = s3 / 'bucket/cases/manifest.json'
        target.write_text('broken JSON')
        for overrides in ({}, {'FAKE_S3_GET_ERROR': 'AccessDenied'}):
            with self.subTest(overrides=overrides):
                result = self.pipeline({**env, **overrides})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(target.read_text(), 'broken JSON')
        self.assertEqual(self.http.requests + self.http.head_requests, [])
        calls = [json.loads(line) for line in (s3 / 'calls.jsonl').read_text().splitlines()]
        self.assertTrue(all(call[:2] == ['s3api', 'get-object'] for call in calls))
