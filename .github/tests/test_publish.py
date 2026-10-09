"""Real temporary Git trees; only external upload is replaced."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import publish as publisher


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.entries = []
        self.write_manifest()
        self.initial = self.commit()
        self.entry = self.add_case('A Case', 'a-case')
        self.write_manifest()
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE).decode().strip()

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'test: fixture')
        return self.git('rev-parse', 'HEAD')

    def add_case(self, title, name):
        entry = {'title': title, 'name': name}
        for kind, ext in [('cover', 'png'), ('case', 'science'), ('introduction', 'md')]:
            file = title + '.' + ext
            path = title + '/' + file
            (self.root / title).mkdir(exist_ok=True)
            (self.root / path).write_bytes(b'original')
            entry[kind] = {'path': path, 'file_name': file, 'bytes': 8, 'sha256': '1' * 64}
            if kind == 'case':
                entry[kind]['release_url'] = ''
        self.entries.append(entry)
        return entry

    def write_manifest(self):
        (self.root / 'manifest.json').write_text(json.dumps(self.entries))

    def change(self, kind='cover', declaration='2'):
        resource = self.entry[kind]
        (self.root / resource['path']).write_bytes(b'modified')  # Same length.
        resource['sha256'] = declaration * 64
        self.write_manifest()
        return self.commit()

    def plan(self, before=None, after=None):
        return publisher.prepare(self.root, before or self.base, after or self.git('rev-parse', 'HEAD'))

    def keys(self, plan):
        return [item['key'] for item in plan['files']]


class PublishTests(GitFixture):
    def test_multiple_commits_and_same_size_changes(self):
        self.change('cover'); after = self.change('introduction', '3')
        self.assertEqual(self.keys(self.plan(after=after)),
                         ['a-case/A Case.png', 'a-case/A Case.md', 'manifest.json'])

    def test_new_directory_publishes_all_local_resources(self):
        self.add_case('B Case', 'b-case'); self.write_manifest(); self.commit()
        self.assertEqual(self.keys(self.plan()), ['b-case/B Case.png', 'b-case/B Case.science',
                                                 'b-case/B Case.md', 'manifest.json'])

    def test_target_change_uploads_new_target_and_preserves_old(self):
        self.entry['name'] = 'renamed'; self.write_manifest(); self.commit()
        self.assertEqual(self.keys(self.plan()), ['renamed/A Case.png', 'renamed/A Case.science',
                                                 'renamed/A Case.md', 'manifest.json'])

    def test_source_switches_and_removal(self):
        self.entry['case']['release_url'] = 'https://example.com/a.science'
        self.write_manifest(); remote = self.commit()
        self.assertEqual(self.keys(self.plan()), ['manifest.json'])
        self.entry['case']['release_url'] = ''; self.write_manifest(); self.commit()
        self.assertEqual(self.keys(self.plan(before=remote)), ['a-case/A Case.science', 'manifest.json'])
        self.entries = []; self.write_manifest(); self.commit()
        self.assertEqual(self.keys(self.plan()), ['manifest.json'])

    def test_description_change_only_uploads_manifest(self):
        self.entry['title'] = 'Description only'; self.write_manifest(); self.commit()
        self.assertEqual(self.keys(self.plan()), ['manifest.json'])

    def test_unlisted_directories_and_file_only_changes_do_not_upload(self):
        self.add_case('Unlisted Case', 'unlisted-case')
        (self.root / self.entry['cover']['path']).write_bytes(b'modified')
        self.commit()
        self.assertEqual(self.keys(self.plan()), [])

    def test_removed_entry_updates_published_manifest_and_preserves_resources(self):
        # Model the existing published objects; the removed case is never read/uploaded.
        objects = {item['key']: (self.root / item['path']).read_bytes()
                   for item in self.plan(before=self.initial)['files']}
        resources = {key: data for key, data in objects.items() if key != 'manifest.json'}
        (self.root / self.entry['cover']['path']).unlink()
        self.entries = []; self.write_manifest(); self.commit()
        plan = self.plan()
        self.assertEqual(self.keys(plan), ['manifest.json'])
        class Upload:
            def upload(inner, path, key):
                objects[key] = path.read_bytes()
        publisher.apply(self.root, plan, Upload())
        self.assertEqual(json.loads(objects['manifest.json']), [])
        self.assertEqual({key: data for key, data in objects.items() if key != 'manifest.json'},
                         resources)

    def test_unchanged_range_empty_and_manifest_formatting_uploads_only_manifest(self):
        self.assertEqual(self.keys(self.plan()), [])
        (self.root / 'manifest.json').write_text(json.dumps(self.entries, indent=2)); self.commit()
        self.assertEqual(self.keys(self.plan()), ['manifest.json'])

    def test_planning_reads_only_git_metadata_and_manifests(self):
        self.change()
        for resource in self.entry.values():
            if isinstance(resource, dict):
                (self.root / resource['path']).unlink()
        # Missing checkout bytes cannot affect planning; no file hashing/stat/S3.
        with patch.object(publisher, 'S3', side_effect=AssertionError('S3 must not be queried')):
            self.assertEqual(self.keys(self.plan()), ['a-case/A Case.png', 'manifest.json'])

    def test_upload_order_and_failure_never_uploads_manifest(self):
        self.change(); plan = self.plan(); calls = []
        class Upload:
            def upload(inner, path, key):
                calls.append(key)
                raise OSError('upload failed')
        with self.assertRaises(OSError):
            publisher.apply(self.root, plan, Upload())
        self.assertEqual(calls, ['a-case/A Case.png'])
        class Success:
            def upload(inner, path, key):
                calls.append((key, path.read_bytes()))
        calls.clear(); publisher.apply(self.root, plan, Success())
        self.assertEqual([key for key, _ in calls], ['a-case/A Case.png', 'manifest.json'])
        self.assertEqual(calls[0][1], b'modified')

    def test_partial_failure_reversion_is_not_repaired_or_cleaned(self):
        failed = self.change()
        self.git('checkout', self.base, '--', 'manifest.json', self.entry['cover']['path'])
        self.add_case('C Case', 'c-case')
        self.entry['cover']['sha256'] = '1' * 64
        self.write_manifest(); self.commit()
        self.assertEqual(self.keys(self.plan()), ['c-case/C Case.png', 'c-case/C Case.science',
                                                 'c-case/C Case.md', 'manifest.json'])

    def test_duplicate_destination_rejected(self):
        self.entries.append(copy.deepcopy(self.entry)); self.write_manifest(); self.commit()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.plan()

    def test_invalid_missing_or_wrong_checkout_ranges_fail(self):
        after = self.change()
        for before, target in [(None, after), ('0' * 40, after), ('f' * 40, after), (after, self.base)]:
            with self.subTest(before=before, target=target), self.assertRaises((ValueError, subprocess.CalledProcessError)):
                publisher.prepare(self.root, before, target)

    def test_cli_upload_only_no_metadata_or_s3_reads(self):
        self.change(); plan = self.plan()
        fake = self.root / 'bin'; fake.mkdir()
        aws = fake / 'aws'
        aws.write_text('#!' + sys.executable + '\n' + (Path(__file__).with_name('fake_s3.py')).read_text())
        aws.chmod(0o755)
        s3 = self.root / 's3'; s3.mkdir()
        plan_file = self.root / 'plan.json'; plan_file.write_text(json.dumps(plan))
        env = {**os.environ, 'PATH': str(fake) + os.pathsep + os.environ['PATH'],
               'FAKE_S3_ROOT': str(s3), 'AWS_TARGET_FOLDER': 's3://bucket/cases'}
        result = subprocess.run([sys.executable, str(SCRIPTS / 'publish.py'), '--apply-plan', str(plan_file)],
                                cwd=self.root, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((s3 / 'bucket/cases/a-case/A Case.png').read_bytes(), b'modified')
        calls = [json.loads(line) for line in (s3 / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[:2] == ['s3', 'cp'] and '--metadata' not in call for call in calls))

    def test_legacy_optional_invalid_sha_does_not_force_historical_migration(self):
        self.entry['case']['sha256'] = {'old': 'optional'}
        self.write_manifest(); legacy = self.commit()
        self.change('cover')
        self.assertEqual(self.keys(self.plan(before=legacy)), ['a-case/A Case.png', 'manifest.json'])

    def test_unchanged_legacy_case_without_introduction_is_not_revalidated(self):
        del self.entry['introduction']; self.write_manifest(); legacy = self.commit()
        (self.root / 'tool.py').write_text('# tooling only'); self.commit()
        self.assertEqual(self.keys(self.plan(before=legacy)), [])
