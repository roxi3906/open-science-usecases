"""Resource paths locate sources independently of catalog classification directories."""
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys

from test_publish import GitFixture, SCRIPTS, publisher
from test_resource_sources import Store
from test_extract_science import package_bytes
import check_structure as checker
import extract_science


class ResourcePathTests(GitFixture):
    def relocate(self, directories):
        for kind, directory in zip(publisher.KINDS, directories):
            resource = self.entry[kind]
            source = self.root / resource['path']
            target = self.root / directory / resource['file_name']
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            resource['path'] = target.relative_to(self.root).as_posix()
        self.write_manifest()
        return self.commit()

    def check_manifest_cli(self):
        selected = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'check_structure.py'),
                                   'directories', '--base', self.base], cwd=self.root,
                                  capture_output=True, text=True, check=True)
        import os
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'check_structure.py'), 'manifest'],
                                cwd=self.root, env={**os.environ, 'CASES_JSON': selected.stdout},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_category_paths_pass_check_and_path_only_publication_keeps_objects(self):
        self.relocate(('catalog/science/physics/covers', 'packages/physics', 'docs/zh/physics'))
        self.check_manifest_cli()
        self.assertEqual(checker.check_declarations(self.root, self.base, self.git('rev-parse', 'HEAD')), [])
        plan = self.plan()
        self.assertEqual(plan['candidates'], [])
        self.assertEqual(plan['packages'], [])
        store = Store(plan['baseline']); store.objects = {'a-case/A Case.png': b'keep'}
        with contextlib.redirect_stdout(io.StringIO()):
            publisher.apply(self.root, plan, store)
        self.assertEqual(store.objects, {'a-case/A Case.png': b'keep'})
        self.assertEqual(set(store.writes), {'manifest.json'})
        self.assertEqual(store.heads, [])

    def test_root_and_old_layout_resources_are_valid_current_and_baseline_paths(self):
        self.relocate(('', 'Old', 'category/subcategory'))
        self.check_manifest_cli()
        for legacy in (False, True):
            self.assertEqual(publisher.read_manifest(json.dumps(self.entries), 'fixture', legacy=legacy), self.entries)
        self.assertEqual(checker.source_errors(self.root, self.entry), [])

    def test_remote_cover_does_not_require_its_directory_when_other_resources_are_local(self):
        self.relocate(('absent/covers', 'packages', 'docs'))
        (self.root / self.entry['cover']['path']).unlink()
        (self.root / 'absent/covers').rmdir(); (self.root / 'absent').rmdir()
        self.entry['cover']['release_url'] = 'https://example.invalid/cover.png'
        self.write_manifest(); self.commit()
        self.assertEqual(checker.schema_errors(self.entry), [])
        self.assertEqual(checker.source_errors(self.root, self.entry), [])
        self.assertTrue(self.plan()['candidates'][0]['force_upload'])

    def test_all_remote_classified_paths_need_no_local_directories(self):
        entry = copy.deepcopy(self.entry)
        for kind in publisher.KINDS:
            entry[kind].update(path='remote/catalog/'+kind+'/'+entry[kind]['file_name'],
                               release_url='https://example.invalid/'+kind+Path(entry[kind]['file_name']).suffix)
        self.assertEqual(checker.schema_errors(entry), [])
        self.assertEqual(checker.source_errors(self.root, entry), [])

    def test_nested_parent_symlink_rejected_for_each_local_resource(self):
        self.relocate(('catalog/covers/deep', 'catalog/packages/deep', 'catalog/docs/deep'))
        for directory in ('covers', 'packages', 'docs'):
            with self.subTest(directory=directory):
                parent = self.root / 'catalog' / directory
                saved = self.root / (directory+'-saved')
                parent.rename(saved)
                parent.symlink_to(saved, target_is_directory=True)
                self.assertTrue(checker.source_errors(self.root, self.entry))
                parent.unlink(); saved.rename(parent)

    def test_arbitrary_paths_still_reject_absolute_traversal_and_drive_names(self):
        for path in ('/A Case.png', '../A Case.png', 'catalog/../A Case.png',
                     'catalog//A Case.png', './A Case.png', 'C:/A Case.png', 'C:A Case.png',
                     'catalog\\A Case.png', 'catalog/\nA Case.png'):
            entry = copy.deepcopy(self.entry)
            entry['cover'].update(path=path, file_name=path.split('/')[-1])
            with self.subTest(path=path):
                self.assertTrue(checker.schema_errors(entry))

    def test_classified_resources_prepare_and_extract_using_each_declared_path(self):
        package = package_bytes()
        path = self.root / self.entry['case']['path']; path.write_bytes(package)
        size, checksum = publisher.file_integrity(path)
        self.entry['case'].update(bytes=size, sha256=checksum)
        after = self.relocate(('covers/physics', 'catalog/science/physics/session', 'introductions/zh'))
        plan = publisher.prepare(self.root, None, after)
        store = Store(None); store.clears = []; store.clear_extracted = store.clears.append
        with contextlib.redirect_stdout(io.StringIO()):
            plan = publisher.prepare_resources(self.root, plan, store, self.root / 'sources')
            plan = extract_science.expand_plan(self.root, plan, self.root / 'staging')
            publisher.apply(self.root, plan, store)
        for kind in publisher.KINDS:
            self.assertIn('a-case/'+self.entry[kind]['file_name'], store.writes)
        self.assertEqual(store.clears, ['a-case'])
        self.assertEqual(store.manifest, (self.root / 'manifest.json').read_text())

    def test_git_treats_special_resource_names_as_literal_paths(self):
        for filename in (':cover.png', 'cover[1]*?.png'):
            with self.subTest(filename=filename):
                resource = self.entry['cover']
                (self.root / resource['path']).rename(self.root / filename)
                resource.update(path=filename, file_name=filename)
                self.write_manifest(); after = self.commit()
                self.assertEqual(checker.check_declarations(self.root, self.base, after), [])
                plan = self.plan(); store = Store(plan['baseline'])
                plan = publisher.prepare_resources(self.root, plan, store, self.root / 'sources')
                with contextlib.redirect_stdout(io.StringIO()):
                    publisher.apply(self.root, plan, store)
                self.assertIn('a-case/'+filename, store.writes)
