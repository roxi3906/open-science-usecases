import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_structure.py"
TITLE = "New AI Case—Does It Work"
NAME = "new-ai-case-does-it-work"
URL = "https://example.com/releases/new-case.science"


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Structure Test")
        self.git("config", "user.email", "structure@example.invalid")
        self.write("manifest.json", "[]")
        self.write("Existing Case/README.md", "Legacy case")
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], cwd=self.root, text=True, stderr=subprocess.STDOUT
        )

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self):
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "test fixture")

    def case(self, remote=False):
        resources = {}
        for key, extension in (("cover", "png"), ("introduction", "md"), ("case", "science")):
            filename = f"{TITLE}.{extension}"
            path = f"{TITLE}/{filename}"
            content = f"fixture {extension}"
            resources[key] = {
                "file_name": filename,
                "path": path,
                "bytes": len(content.encode()),
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
            if key != "case" or not remote:
                self.write(path, content)
        resources["case"]["release_url"] = URL if remote else ""
        entry = {"title": TITLE, "name": NAME, **resources}
        self.manifest([entry])
        return entry

    def manifest(self, entries):
        self.write("manifest.json", json.dumps(entries, ensure_ascii=False))

    def run_stage(self, stage, *args, cases=None, extra_env=None):
        output = self.root / ".step-output"
        output.write_text("")
        env = {**os.environ, "GITHUB_OUTPUT": str(output), "GITHUB_ACTIONS": "false"}
        env.pop("CASES_JSON", None)
        if cases is not None:
            env["CASES_JSON"] = json.dumps(cases)
        env.update(extra_env or {})
        result = subprocess.run(
            [sys.executable, str(SCRIPT), stage, *args], cwd=self.root,
            env=env, text=True, capture_output=True,
        )
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        return result, values

    def scan(self, **kwargs):
        return self.run_stage("directories", "--base", self.base, **kwargs)

    def check_manifest(self, expected=0):
        scan, values = self.scan()
        self.assertEqual(scan.returncode, 0, scan.stdout + scan.stderr)
        result, _ = self.run_stage("manifest", cases=json.loads(values["cases"]))
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_local_case_passes_both_stages_and_exports_state(self):
        self.case()
        self.commit()
        result, values = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(values["cases"]), [{
            "directory": TITLE, "name": NAME, "valid": True,
            "has_local_science": True,
        }])
        self.check_manifest()

    def test_remote_case_without_readme_passes_using_manifest_url(self):
        self.case(remote=True)
        self.commit()
        result, values = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(json.loads(values["cases"])[0]["has_local_science"])
        self.check_manifest()

    def test_existing_directories_and_hidden_tooling_are_not_new_cases(self):
        self.write("Existing Case/nested/new.txt", "added")
        self.write(".github/workflows/check.yml", "name: test")
        self.commit()
        result, values = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(values["cases"]), [])
        self.check_manifest()

    def test_required_files_cannot_be_replaced_by_readme_or_wrong_names(self):
        self.case()
        self.commit()
        for extension in ("md", "png"):
            path = self.root / TITLE / f"{TITLE}.{extension}"
            renamed = path.with_name(f"wrong.{extension}")
            path.rename(renamed)
            with self.subTest(extension=extension):
                result, values = self.scan()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertFalse(json.loads(values["cases"])[0]["valid"])
            renamed.rename(path)

    def test_remote_readme_is_not_read_or_used_for_validation(self):
        self.case(remote=True)
        self.commit()
        for content in (b"no download", b"[Download](https://example.com/other.science)", b"\xff"):
            with self.subTest(content=content):
                (self.root / TITLE / "README.md").write_bytes(content)
                self.check_manifest()

    def test_remote_manifest_url_accepts_science_paths_with_query_or_fragment(self):
        entry = self.case(remote=True)
        self.commit()
        for url in (URL, "http://example.com/other.science", "https://[::1]/case.science",
                    "https://example.com:443/case.science", URL + "?download=1#asset",
                    URL + "?token=abc!", URL + "#asset;"):
            with self.subTest(url=url):
                entry["case"]["release_url"] = url
                self.manifest([entry])
                self.check_manifest()

    def test_missing_duplicate_or_wrong_manifest_name_fails(self):
        entry = self.case()
        self.commit()
        for entries in ([], [entry, entry], [{**entry, "name": "wrong-name"}]):
            with self.subTest(entries=entries):
                self.manifest(entries)
                self.check_manifest(expected=1)

    def test_manifest_keys_types_paths_and_hashes_are_checked(self):
        entry = self.case()
        self.commit()
        mutations = [
            ((), "title", "Wrong title"), ((), "introduction", None),
            ((), "unexpected", True), (("cover",), "file_name", "wrong.png"),
            (("cover",), "path", "../outside.png"), (("case",), "bytes", True),
            (("case",), "bytes", -1), (("case",), "bytes", "15"),
            (("cover",), "bytes", 999), (("introduction",), "sha256", "0" * 64),
            (("case",), "sha256", "not-a-hash"), (("case",), "release_url", URL),
        ]
        for parents, key, value in mutations:
            with self.subTest(parents=parents, key=key, value=value):
                changed = copy.deepcopy(entry)
                target = changed
                for parent in parents:
                    target = target[parent]
                target[key] = value
                self.manifest([changed])
                self.check_manifest(expected=1)

    def test_missing_keys_fail_at_each_level(self):
        entry = self.case()
        self.commit()
        for parent in (None, "cover", "case", "introduction"):
            target = entry if parent is None else entry[parent]
            for key in target:
                with self.subTest(parent=parent, key=key):
                    changed = copy.deepcopy(entry)
                    del (changed if parent is None else changed[parent])[key]
                    self.manifest([changed])
                    self.check_manifest(expected=1)

    def test_remote_release_url_must_be_a_nonempty_science_url(self):
        entry = self.case(remote=True)
        self.commit()
        for url in ("", None, 12, False, [], {}, " ", "file.science",
                    "https://example.com/file.zip", "https://example.com/page?file=.science",
                    "https://example.com/file.science/", "ftp://example.com/file.science",
                    "https:///file.science", "https://[invalid]/file.science",
                    "https://example.com:not-a-port/file.science", "https://example.com:99999/file.science",
                    "https://example.com/file.science\n", "https://exa mple.com/file.science"):
            with self.subTest(url=url):
                entry["case"]["release_url"] = url
                self.manifest([entry])
                result = self.check_manifest(expected=1)
                self.assertIn("release_url", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_readme_cannot_replace_missing_manifest_release_url(self):
        entry = self.case(remote=True)
        del entry["case"]["release_url"]
        self.manifest([entry])
        self.write(f"{TITLE}/README.md", f"[Download]({URL})")
        self.commit()
        self.check_manifest(expected=1)

    def test_wrongly_named_science_file_still_requires_release_url(self):
        self.case()
        (self.root / TITLE / f"{TITLE}.science").rename(self.root / TITLE / "wrong.science")
        self.commit()
        self.check_manifest(expected=1)

    def test_malformed_manifest_fails_cleanly(self):
        self.case()
        self.commit()
        for content in ("{", "{}", "[null]", '[{"name": "x", "name": "y"}]'):
            with self.subTest(content=content):
                self.write("manifest.json", content)
                result = self.check_manifest(expected=1)
                self.assertNotIn("Traceback", result.stderr)

    def test_invalid_base_fails_instead_of_silently_skipping(self):
        result, _ = self.run_stage("directories", "--base", "nonexistent-ref")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("nonexistent-ref", result.stderr)
        self.assertIn("fetch-depth: 0", result.stderr)

    def test_missing_file_log_identifies_case_and_tells_how_to_fix_it(self):
        self.case()
        (self.root / TITLE / f"{TITLE}.png").unlink()
        self.commit()
        result, _ = self.scan()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{TITLE}/{TITLE}.png", result.stderr)
        self.assertIn("Add", result.stderr)
        self.assertIn("same name", result.stderr)

    def test_metadata_log_shows_current_values_and_replacements(self):
        entry = self.case()
        entry["cover"]["bytes"] = 12345
        entry["cover"]["sha256"] = "0" * 64
        self.manifest([entry])
        self.commit()
        result = self.check_manifest(expected=1)
        self.assertIn("12345", result.stderr)
        self.assertIn(str(len(b"fixture png")), result.stderr)
        self.assertIn("0" * 64, result.stderr)
        self.assertIn(hashlib.sha256(b"fixture png").hexdigest(), result.stderr)
        self.assertIn("Update", result.stderr)
        self.assertIn("2 problems", result.stderr)

    def test_manifest_json_error_includes_location_and_fix(self):
        self.case()
        self.commit()
        self.write("manifest.json", '[\n{"name": }\n]')
        result = self.check_manifest(expected=1)
        self.assertIn("manifest.json", result.stderr)
        self.assertIn("line 2", result.stderr)
        self.assertIn("column", result.stderr)
        self.assertIn("JSON", result.stderr)

    def test_workflow_input_errors_explain_step_wiring(self):
        for extra in ({}, {"CASES_JSON": "{"}, {"CASES_JSON": "[null]"},
                      {"CASES_JSON": '[{"directory": "New Case"}]'}):
            with self.subTest(extra=extra):
                result, _ = self.run_stage("manifest", extra_env=extra)
                self.assertEqual(result.returncode, 1)
                self.assertIn("CASES_JSON", result.stderr)
                self.assertIn("directories", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_actions_error_annotations_are_escaped_and_group_is_closed(self):
        entry = self.case()
        entry["cover"]["path"] = "wrong%0A\n::warning::injected"
        self.manifest([entry])
        self.commit()
        scan, values = self.scan()
        self.assertEqual(scan.returncode, 0)
        result, _ = self.run_stage("manifest", cases=json.loads(values["cases"]),
                                   extra_env={"GITHUB_ACTIONS": "true"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("::group::", result.stderr)
        annotations = [line for line in result.stderr.splitlines() if line.startswith("::error")]
        self.assertEqual(len(annotations), 1)
        self.assertIn("%250A", annotations[0])
        self.assertNotIn("\n::warning::injected", result.stderr)
        self.assertTrue(result.stderr.rstrip().endswith("::endgroup::"))

    def test_no_new_cases_explains_skip_and_stdout_stays_json(self):
        result, _ = self.scan()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), [])
        self.assertIn("No new case directories", result.stderr)

    def test_directory_names_cannot_inject_legacy_actions_commands(self):
        directory = "Case ##[error]injected ##[endgroup]"
        for extension in ("md", "png", "science"):
            self.write(f"{directory}/{directory}.{extension}", "fixture")
        self.commit()
        result, values = self.scan(extra_env={"GITHUB_ACTIONS": "true"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("##[", result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["directory"], directory)
        self.assertEqual(json.loads(values["cases"])[0]["directory"], directory)
        self.assertEqual(result.stderr.count("::endgroup::"), 1)

    def test_initial_repository_push_checks_all_directories(self):
        self.case()
        self.commit()
        result, values = self.run_stage("directories", "--base", "0" * 40)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual([case["directory"] for case in json.loads(values["cases"])],
                         ["Existing Case", TITLE])

    def test_first_feature_branch_push_does_not_recheck_legacy_cases(self):
        self.case()
        self.commit()
        result, values = self.run_stage(
            "directories", "--base", "0" * 40, "--new-branch-base", self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([case["directory"] for case in json.loads(values["cases"])], [TITLE])

    def test_first_feature_branch_push_without_new_cases_passes(self):
        result, values = self.run_stage(
            "directories", "--base", "0" * 40, "--new-branch-base", self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(values["cases"]), [])

    def test_pr_uses_common_ancestor_when_base_branch_diverged(self):
        self.git("checkout", "-qb", "base-branch")
        self.case()
        self.commit()
        base_head = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-qb", "proposed-branch", self.base)
        self.case()
        (self.root / TITLE / f"{TITLE}.png").unlink()
        self.commit()
        result, values = self.run_stage("directories", "--base", base_head, "--merge-base")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(json.loads(values["cases"])[0]["directory"], TITLE)

    def test_multiple_directories_cannot_share_the_same_kebab_case_name(self):
        self.case()
        other = TITLE.replace("—", " ")
        for extension in ("md", "png", "science"):
            self.write(f"{other}/{other}.{extension}", "fixture")
        self.commit()
        result = self.check_manifest(expected=1)
        self.assertIn("multiple new directories", result.stderr)

    def test_renamed_case_is_a_new_directory(self):
        self.git("mv", "Existing Case", "Renamed Case")
        self.commit()
        result, values = self.scan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(json.loads(values["cases"])[0]["directory"], "Renamed Case")

    def test_symlink_resources_do_not_count_as_case_files(self):
        self.case()
        cover = self.root / TITLE / f"{TITLE}.png"
        cover.unlink()
        cover.symlink_to("../manifest.json")
        self.commit()
        result, _ = self.scan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
