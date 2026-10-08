import copy
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/publish.py'
spec = importlib.util.spec_from_file_location('publisher', SCRIPT)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class MemoryS3:
    """Replace only the S3 boundary; Git, files, planning and publishing stay real."""
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.fail_key = None

    def head(self, key):
        self.calls.append(('head', key))
        obj = self.objects.get(key)
        return copy.deepcopy(obj['head']) if obj else None

    def tags(self, key):
        self.calls.append(('tags', key))
        return copy.deepcopy(self.objects[key]['tags'])

    def tag(self, key, tags):
        self.calls.append(('tag', key))
        self.objects[key]['tags'] = copy.deepcopy(tags)

    def download(self, key, path):
        self.calls.append(('get', key))
        path.write_bytes(self.objects[key]['body'])

    def upload(self, path, key, sha256, commit):
        self.calls.append(('upload', key))
        if key == self.fail_key:
            raise ValueError('Simulated upload failure')
        self.seed(key, path.read_bytes(), commit)

    def seed(self, key, body, commit=None, checksum=True):
        metadata = {'sha256': hashlib.sha256(body).hexdigest()} if checksum else {}
        if commit:
            metadata['source-commit'] = commit
        self.objects[key] = {'body': body, 'head': {'ContentLength': len(body), 'Metadata': metadata}, 'tags': []}


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.cases = []
        self.write_manifest()
        self.initial = self.commit()
        self.case = self.add_case('A Case', 'a-case')
        self.write_manifest()
        self.base = self.commit()
        self.s3 = MemoryS3()

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE).decode().strip()

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'test: fixture')
        return self.git('rev-parse', 'HEAD')

    def add_case(self, title, name):
        case = {'title': title, 'name': name}
        for key, extension in [('cover', 'png'), ('case', 'science'), ('introduction', 'md')]:
            filename = title + '.' + extension
            path = self.root / title / filename
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'fixture ' + extension.encode())
            case[key] = {'path': str(path.relative_to(self.root)), 'file_name': filename,
                         'bytes': path.stat().st_size, 'sha256': 'optional and untrusted'}
        case['case']['release_url'] = ''
        self.cases.append(case)
        return case

    def write_manifest(self):
        (self.root / 'manifest.json').write_text(json.dumps(self.cases))

    def change(self, key, content=None):
        path = self.root / self.case[key]['path']
        path.write_bytes(content or b'x' * path.stat().st_size)
        return path

    def plan(self, before=None, after=None):
        return publisher.prepare(self.root, before or self.base, after or self.git('rev-parse', 'HEAD'), self.s3)

    def apply(self, plan):
        publisher.apply(self.root, plan, self.s3)

    def uploads(self):
        return [key for operation, key in self.s3.calls if operation == 'upload']

    def test_multi_commit_push_includes_first_and_last_commit(self):
        self.change('cover'); self.commit()
        self.change('introduction'); after = self.commit()
        plan = self.plan(after=after)
        self.assertEqual([item['key'] for item in plan['files']], ['a-case/A Case.png', 'a-case/A Case.md'])
        self.apply(plan)
        self.assertEqual(self.uploads(), ['a-case/A Case.png', 'a-case/A Case.md'])

    def test_same_size_content_change_replaces_only_that_file(self):
        path = self.root / self.case['cover']['path']
        self.s3.seed('a-case/A Case.png', path.read_bytes(), self.base)
        previous = path.stat()
        self.change('cover'); os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        self.commit()
        self.apply(self.plan())
        self.assertEqual(self.uploads(), ['a-case/A Case.png'])
        self.assertEqual(self.s3.objects['a-case/A Case.png']['body'], path.read_bytes())

    def test_new_directory_uploads_its_resources_and_manifest_last(self):
        self.add_case('New Case', 'new-case'); self.write_manifest(); self.commit()
        self.apply(self.plan())
        self.assertEqual(self.uploads(), ['new-case/New Case.png', 'new-case/New Case.science',
                                         'new-case/New Case.md', 'manifest.json'])

    def test_empty_diff_skips_s3_and_missing_historical_files(self):
        self.commit()
        (self.root / self.case['cover']['path']).unlink()
        plan = self.plan()
        self.assertEqual(plan['files'], [])
        self.assertEqual(self.s3.calls, [])

    def test_only_changed_manifest_does_not_read_historical_resources(self):
        self.case['title'] = 'Metadata change'; self.write_manifest(); self.commit()
        (self.root / self.case['case']['path']).unlink()
        with patch.object(publisher, 'digest', wraps=publisher.digest) as digest:
            self.apply(self.plan())
        self.assertTrue(all(call.args[0].name == 'manifest.json' for call in digest.call_args_list))
        self.assertEqual(self.uploads(), ['manifest.json'])

    def test_unlisted_changed_files_are_ignored(self):
        (self.root / 'README.md').write_text('not published')
        (self.root / 'A Case/unlisted.txt').write_text('not published')
        self.commit()
        self.assertEqual(self.plan()['files'], [])
        self.assertEqual(self.s3.calls, [])

    def test_known_same_s3_content_skips_upload_and_advances_version_tag(self):
        path = self.change('cover'); after = self.commit()
        self.s3.seed('a-case/A Case.png', path.read_bytes(), self.base)
        self.s3.objects['a-case/A Case.png']['tags'] = [{'Key': 'owner', 'Value': 'retained'}]
        plan = self.plan()
        self.assertEqual(plan['files'], [])
        publisher.record_matches(self.root, plan, self.s3)
        self.assertEqual(self.uploads(), [])
        self.assertNotIn(('get', 'a-case/A Case.png'), self.s3.calls)
        self.assertEqual(self.s3.objects['a-case/A Case.png']['tags'], [
            {'Key': 'owner', 'Value': 'retained'}, {'Key': 'open-science-commit', 'Value': after}])

    def test_manual_s3_object_without_checksum_is_compared_only_for_candidate(self):
        path = self.change('cover'); self.commit()
        self.s3.seed('a-case/A Case.png', path.read_bytes(), checksum=False)
        self.assertEqual(self.plan()['files'], [])
        self.assertEqual([call for call in self.s3.calls if call[0] == 'get'], [('get', 'a-case/A Case.png')])

    def test_unknown_same_size_s3_content_is_not_assumed_equal(self):
        path = self.change('cover'); self.commit()
        self.s3.seed('a-case/A Case.png', b'z' * path.stat().st_size, checksum=False)
        self.apply(self.plan())
        self.assertEqual(self.uploads(), ['a-case/A Case.png'])

    def test_continuous_pushes_keep_distinct_files_and_prevent_old_overwrite(self):
        self.change('cover'); first = self.commit()
        first_plan = self.plan(after=first)
        self.change('cover', b'y' * 11); self.change('introduction'); second = self.commit()
        self.apply(self.plan(before=first, after=second))
        self.s3.calls.clear()
        self.git('checkout', '--detach', first)
        self.apply(first_plan)
        self.assertEqual(self.uploads(), [])
        self.assertEqual(self.s3.objects['a-case/A Case.png']['body'], b'y' * 11)

    def test_earlier_push_still_uploads_distinct_file_after_newer_push(self):
        self.change('cover'); first = self.commit()
        self.change('introduction'); second = self.commit()
        self.apply(self.plan(before=first, after=second))
        self.s3.calls.clear()
        self.git('checkout', '--detach', first)
        self.apply(self.plan(after=first))
        self.assertEqual(self.uploads(), ['a-case/A Case.png'])

    def test_same_content_newer_push_cannot_be_overwritten_by_old_retry(self):
        original = (self.root / self.case['cover']['path']).read_bytes()
        self.s3.seed('a-case/A Case.png', original, self.base)
        self.change('cover'); first = self.commit()
        self.change('cover', original); second = self.commit()
        plan = self.plan(before=first, after=second)
        self.assertEqual(plan['files'], [])
        publisher.record_matches(self.root, plan, self.s3)
        self.git('checkout', '--detach', first)
        self.apply(self.plan(after=first))
        self.assertEqual(self.uploads(), [])
        self.assertEqual(self.s3.objects['a-case/A Case.png']['body'], original)

    def test_failure_retry_uses_original_range_and_keeps_manifest_last(self):
        self.change('cover'); self.change('introduction')
        self.case['title'] = 'Changed'; self.write_manifest(); after = self.commit()
        plan = self.plan()
        self.s3.fail_key = 'a-case/A Case.md'
        with self.assertRaisesRegex(ValueError, 'Simulated'):
            self.apply(plan)
        self.assertNotIn('manifest.json', self.uploads())
        self.s3.fail_key = None; self.s3.calls.clear()
        self.apply(plan)
        self.assertEqual(plan['after'], after)
        self.assertEqual(self.uploads(), ['a-case/A Case.md', 'manifest.json'])

    def test_unknown_or_zero_range_is_error_without_s3_access(self):
        for before in ('', 'HEAD^', '0' * 40, 'f' * 40):
            with self.subTest(before=before), self.assertRaises((ValueError, subprocess.CalledProcessError)):
                publisher.prepare(self.root, before, self.base, self.s3)
        self.assertEqual(self.s3.calls, [])

    def test_checkout_must_match_original_after_even_if_branch_has_advanced(self):
        self.change('cover'); after = self.commit()
        self.change('introduction'); self.commit()
        with self.assertRaisesRegex(ValueError, 'checkout'):
            self.plan(after=after)
        self.git('checkout', '--detach', after)
        self.assertEqual([item['key'] for item in self.plan(after=after)['files']], ['a-case/A Case.png'])

    def test_plan_rejects_changed_local_bytes_before_any_upload(self):
        self.change('cover'); self.commit(); plan = self.plan()
        self.change('cover', b'z' * 11)
        with self.assertRaisesRegex(ValueError, 'checkout|changed'):
            self.apply(plan)
        self.assertEqual(self.uploads(), [])

    def test_changed_remote_package_uses_only_head(self):
        requests = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_HEAD(self):
                requests.append('HEAD'); self.send_response(200); self.end_headers()
            def do_GET(self):
                requests.append('GET'); self.send_error(500)
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(thread.join); self.addCleanup(server.shutdown)
        (self.root / self.case['case']['path']).unlink()
        self.case['case']['release_url'] = 'http://127.0.0.1:%s/asset.science' % server.server_port
        self.write_manifest(); self.commit()
        self.apply(self.plan())
        self.assertEqual(requests, ['HEAD'])
        self.assertEqual(self.uploads(), ['manifest.json'])

    def test_cli_requires_explicit_range_and_has_no_full_sync_fallback(self):
        result = subprocess.run([sys.executable, str(SCRIPT), '--dry-run'], cwd=self.root,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_cli_prepares_skips_and_retries_with_real_s3_command_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            binary = store / 'aws'
            fake = SCRIPT.parents[1] / 'tests/fake_s3.py'
            binary.write_text('#!' + sys.executable + '\n' + fake.read_text())
            binary.chmod(0o755)
            output = store / 'output'
            plan_path = store / 'plan.json'
            env = dict(os.environ, PATH=str(store) + os.pathsep + os.environ['PATH'],
                       FAKE_S3_ROOT=str(store), AWS_TARGET_FOLDER='s3://bucket/prefix',
                       GITHUB_OUTPUT=str(output))
            self.change('cover'); after = self.commit()
            def run(*args):
                result = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result
            run('--before', self.base, '--after', after, '--prepare', str(plan_path))
            self.assertIn('has_uploads=true\n', output.read_text())
            self.assertNotIn('s3://bucket', output.read_text())
            self.assertFalse((store / 'bucket').exists())
            run('--apply-plan', str(plan_path))
            target = store / 'bucket/prefix/a-case/A Case.png'
            self.assertEqual(target.read_bytes(), b'x' * 11)
            self.assertEqual(json.loads(target.with_name(target.name + '.meta').read_text())['source-commit'], after)
            output.write_text('')
            run('--before', self.base, '--after', after, '--prepare', str(plan_path))
            self.assertIn('has_uploads=false\n', output.read_text())
            calls = [json.loads(line) for line in (store / 'calls.jsonl').read_text().splitlines()]
            self.assertEqual(sum(call[:2] == ['s3', 'cp'] for call in calls), 1)
            # Removing the checksum emulates a manually synchronized object.
            target.with_name(target.name + '.meta').write_text('{}')
            run('--before', self.base, '--after', after, '--prepare', str(plan_path))
            self.assertEqual(json.loads(plan_path.read_text())['files'], [])

    def test_manifest_from_older_retry_cannot_replace_newer_manifest(self):
        self.change('cover'); self.case['title'] = 'First'; self.write_manifest(); first = self.commit()
        first_plan = self.plan(after=first)
        self.change('introduction'); self.case['title'] = 'Second'; self.write_manifest(); second = self.commit()
        self.apply(self.plan(before=first, after=second))
        latest_manifest = self.s3.objects['manifest.json']['body']
        self.s3.calls.clear(); self.git('checkout', '--detach', first)
        self.apply(first_plan)
        self.assertEqual(self.uploads(), ['a-case/A Case.png'])
        self.assertEqual(self.s3.objects['manifest.json']['body'], latest_manifest)

    def test_s3_errors_other_than_not_found_fail_closed(self):
        s3 = publisher.S3('s3://bucket/prefix')
        for message in ('AccessDenied', 'connection timed out'):
            result = subprocess.CompletedProcess([], 1, '', message)
            with patch.object(publisher.subprocess, 'run', return_value=result), self.assertRaises(ValueError):
                s3.head('file.png')
        result = subprocess.CompletedProcess([], 1, '', 'An error occurred (404) when calling HeadObject')
        with patch.object(publisher.subprocess, 'run', return_value=result):
            self.assertIsNone(s3.head('file.png'))


if __name__ == '__main__':
    unittest.main()
