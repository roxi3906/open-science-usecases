import hashlib
import copy
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish.py"


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = self.root / "uploads.jsonl"
        self.objects = self.root / "s3"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        aws = bin_dir / "aws"
        aws.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "with open(os.environ['UPLOAD_LOG'], 'a') as f:\n"
            "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if os.environ.get('UPLOAD_EXIT', '0') != '0':\n"
            "    sys.exit(int(os.environ['UPLOAD_EXIT']))\n"
            "assert sys.argv[1:3] == ['s3', 'cp']\n"
            "assert sys.argv[4].startswith('s3://')\n"
            "target = Path(os.environ['S3_ROOT']) / sys.argv[4][5:]\n"
            "target.parent.mkdir(parents=True, exist_ok=True)\n"
            "target.write_bytes(Path(sys.argv[3]).read_bytes())\n"
        )
        aws.chmod(0o755)
        self.env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                        UPLOAD_LOG=str(self.calls), S3_ROOT=str(self.objects),
                        AWS_TARGET_FOLDER="s3://test-bucket/cases/")
        self.case = {"name": "a-test-case", "title": "A Test Case"}
        for key, suffix in [("cover", "png"), ("case", "science"), ("introduction", "md")]:
            name = "A Test—Case." + suffix
            path = self.root / "A Test Case" / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"sample " + suffix.encode())
            self.case[key] = {
                "file_name": name, "path": str(path.relative_to(self.root)),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        self.case["case"]["release_url"] = ""

    def run_publish(self, *args, cases=None):
        (self.root / "manifest.json").write_text(json.dumps(cases or [self.case]))
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                              env=self.env, capture_output=True, text=True)

    def uploads(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True).strip()

    def baseline(self, cases=None):
        # Use real Git blobs so equal-size edits cannot hide behind file metadata.
        (self.root / "manifest.json").write_text(json.dumps(cases or [self.case]))
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("add", "manifest.json", "A Test Case")
        self.git("commit", "-qm", "test: baseline")
        return self.git("rev-parse", "HEAD")

    def test_unchanged_publication_has_no_uploads(self):
        base = self.baseline()
        result = self.run_publish("--base", base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.uploads(), [])

    def test_content_only_change_replaces_just_one_same_size_file(self):
        base = self.baseline()
        path = self.root / self.case["introduction"]["path"]
        previous = path.stat()
        path.write_bytes(b"x" * previous.st_size)
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        result = self.run_publish("--base", base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[3] for call in self.uploads()], [
            "s3://test-bucket/cases/a-test-case/A Test—Case.md",
        ])

    def test_manifest_only_change_does_not_reupload_resources(self):
        base = self.baseline()
        self.case["title"] = "Updated title"
        result = self.run_publish("--base", base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[3] for call in self.uploads()], [
            "s3://test-bucket/cases/manifest.json",
        ])

    def test_new_directory_uploads_only_new_resources_then_manifest(self):
        base = self.baseline()
        new_case = copy.deepcopy(self.case)
        new_case.update(name="new-case", title="New Case")
        for key, suffix in [("cover", "png"), ("case", "science"), ("introduction", "md")]:
            path = self.root / "New Case" / ("New Case." + suffix)
            path.parent.mkdir(exist_ok=True)
            path.write_bytes((self.root / self.case[key]["path"]).read_bytes())
            new_case[key].update(file_name=path.name, path=str(path.relative_to(self.root)))
        result = self.run_publish("--base", base, cases=[self.case, new_case])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[3] for call in self.uploads()], [
            "s3://test-bucket/cases/new-case/New Case.png",
            "s3://test-bucket/cases/new-case/New Case.science",
            "s3://test-bucket/cases/new-case/New Case.md",
            "s3://test-bucket/cases/manifest.json",
        ])

    def test_renamed_destination_uploads_even_when_contents_are_identical(self):
        base = self.baseline()
        self.case["introduction"]["file_name"] = "Renamed.md"
        result = self.run_publish("--base", base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[3] for call in self.uploads()], [
            "s3://test-bucket/cases/a-test-case/Renamed.md",
            "s3://test-bucket/cases/manifest.json",
        ])

    def test_prepare_exports_empty_plan_without_aws_credentials_or_remote_head(self):
        self.remote()
        (self.root / self.case["case"]["path"]).unlink()
        base = self.baseline()
        self.env.pop("AWS_TARGET_FOLDER")
        output = self.root / "output"
        self.env["GITHUB_OUTPUT"] = str(output)
        result = self.run_publish("--base", base, "--prepare", str(self.root / "plan.json"))
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads((self.root / "plan.json").read_text())
        self.assertEqual(plan["files"], [])
        self.assertIn("has_uploads=false\n", output.read_text())
        self.assertEqual(self.uploads(), [])
        self.assertEqual(self.requests, [])

    def test_apply_uses_prepared_plan_and_rejects_content_changes_after_preparation(self):
        base = self.baseline()
        path = self.root / self.case["cover"]["path"]
        path.write_bytes(b"x" * path.stat().st_size)
        plan = self.root / "plan.json"
        result = self.run_publish("--base", base, "--prepare", str(plan))
        self.assertEqual(result.returncode, 0, result.stderr)
        path.write_bytes(b"y" * path.stat().st_size)
        result = self.run_publish("--apply-plan", str(plan))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.uploads(), [])

    def test_prepared_plan_uploads_only_changed_files_and_changed_manifest_last(self):
        base = self.baseline()
        path = self.root / self.case["cover"]["path"]
        path.write_bytes(b"x" * path.stat().st_size)
        self.case["title"] = "Updated title"
        plan = self.root / "plan.json"
        output = self.root / "output"
        self.env["GITHUB_OUTPUT"] = str(output)
        result = self.run_publish("--base", base, "--prepare", str(plan))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("has_uploads=true\n", output.read_text())
        self.assertNotIn("test-bucket", output.read_text())
        self.assertEqual(self.uploads(), [])
        result = self.run_publish("--apply-plan", str(plan))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[3] for call in self.uploads()], [
            "s3://test-bucket/cases/a-test-case/A Test—Case.png",
            "s3://test-bucket/cases/manifest.json",
        ])
        self.assertEqual((self.objects / "test-bucket/cases/a-test-case/A Test—Case.png").read_bytes(),
                         path.read_bytes())

    def test_readme_change_has_no_uploads(self):
        base = self.baseline()
        (self.root / "A Test Case" / "README.md").write_text("Documentation only")
        result = self.run_publish("--base", base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.uploads(), [])

    def test_removed_case_updates_manifest_without_deleting_s3_objects(self):
        base = self.baseline()
        self.assertEqual(self.run_publish().returncode, 0)
        self.calls.unlink()
        (self.root / "manifest.json").write_text("[]")
        result = subprocess.run([sys.executable, str(SCRIPT), "--base", base],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[3] for call in self.uploads()], ["s3://test-bucket/cases/manifest.json"])
        self.assertTrue((self.objects / "test-bucket/cases/a-test-case/A Test—Case.png").is_file())

    def test_invalid_baseline_fails_before_uploading(self):
        self.baseline()
        result = self.run_publish("--base", "f" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.uploads(), [])

    def test_remote_url_change_updates_manifest_without_uploading_remote_package(self):
        self.remote()
        (self.root / self.case["case"]["path"]).unlink()
        base = self.baseline()
        self.case["case"]["release_url"] += "?v=2"
        result = self.run_publish("--base", base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[3] for call in self.uploads()], ["s3://test-bucket/cases/manifest.json"])
        self.assertEqual(self.requests, [("HEAD", "/redirect?v=2")])

    def test_retry_includes_changes_since_successful_baseline(self):
        base = self.baseline()
        cover = self.root / self.case["cover"]["path"]
        cover.write_bytes(b"x" * cover.stat().st_size)
        self.env["UPLOAD_EXIT"] = "1"
        result = self.run_publish("--base", base)
        self.assertNotEqual(result.returncode, 0)
        self.calls.unlink()
        del self.env["UPLOAD_EXIT"]
        intro = self.root / self.case["introduction"]["path"]
        intro.write_bytes(b"y" * intro.stat().st_size)
        result = self.run_publish("--base", base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[3] for call in self.uploads()], [
            "s3://test-bucket/cases/a-test-case/A Test—Case.png",
            "s3://test-bucket/cases/a-test-case/A Test—Case.md",
        ])

    def remote(self, status=200, etag='"remote-tag"'):
        self.requests = []
        requests = self.requests

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_HEAD(self):
                requests.append(("HEAD", self.path))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/asset")
                else:
                    self.send_response(status)
                    self.send_header("Content-Length", "681599670")
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("ETag", etag)
                self.end_headers()

            def do_GET(self):
                requests.append(("GET", self.path))
                self.send_error(500, "GET is forbidden")

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.case["case"]["release_url"] = "http://127.0.0.1:%s/redirect" % server.server_port

    def test_uploads_manifest_resources_under_name_with_original_filenames(self):
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({call[3] for call in self.uploads()}, {
            "s3://test-bucket/cases/a-test-case/A Test—Case.png",
            "s3://test-bucket/cases/a-test-case/A Test—Case.science",
            "s3://test-bucket/cases/a-test-case/A Test—Case.md",
            "s3://test-bucket/cases/manifest.json",
        })
        for call in self.uploads():
            self.assertEqual(call[:2], ["s3", "cp"])
            self.assertTrue(Path(call[2]).is_file())

    def test_remote_resource_uses_head_even_after_redirect_and_never_uploads(self):
        self.remote()
        (self.root / self.case["case"]["path"]).unlink()
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests, [("HEAD", "/redirect"), ("HEAD", "/asset")])
        self.assertEqual(len(self.uploads()), 3)
        self.assertFalse(any(call[3].endswith(".science") for call in self.uploads()))
        self.assertIn("681599670", result.stdout)
        self.assertIn("remote-tag", result.stdout)

    def test_local_file_and_release_url_together_block_publish_without_head(self):
        self.remote()
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("both", result.stderr)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.uploads(), [])

    def test_missing_local_file_and_empty_release_url_block_publish(self):
        (self.root / self.case["case"]["path"]).unlink()
        for release_url in ("", " "):
            with self.subTest(release_url=release_url):
                self.case["case"]["release_url"] = release_url
                result = self.run_publish()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("neither", result.stderr)
                self.assertEqual(self.uploads(), [])

    def test_non_utf8_head_headers_do_not_block_local_uploads(self):
        self.remote(etag='"caf\xe9\x85"')
        (self.root / self.case["case"]["path"]).unlink()
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests, [("HEAD", "/redirect"), ("HEAD", "/asset")])
        self.assertEqual(len(self.uploads()), 3)
        metadata = json.loads(result.stdout.split("\n")[0])
        self.assertEqual(metadata["headers"]["etag"], '"caf\xe9\x85"')

    def test_head_failure_does_not_fall_back_to_get_or_block_local_uploads(self):
        self.remote(status=405)
        (self.root / self.case["case"]["path"]).unlink()
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests, [("HEAD", "/redirect"), ("HEAD", "/asset")])
        self.assertEqual(len(self.uploads()), 3)
        self.assertIn("HEAD unavailable", result.stdout)
        self.assertIn(self.case["case"]["sha256"], result.stdout)

    def test_missing_optional_introduction_is_allowed(self):
        del self.case["introduction"]
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.uploads()), 3)

    def test_manifest_is_uploaded_to_target_root_after_all_resources(self):
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.uploads()[-1][3], "s3://test-bucket/cases/manifest.json")
        self.assertEqual((self.objects / "test-bucket/cases/manifest.json").read_bytes(),
                         (self.root / "manifest.json").read_bytes())

    def test_republish_replaces_same_size_file_and_manifest_with_latest_bytes(self):
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.objects / "test-bucket/cases/manifest.json").is_file())
        path = self.root / self.case["introduction"]["path"]
        previous = path.stat()
        content = b"x" * previous.st_size
        path.write_bytes(content)
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        self.case["introduction"]["sha256"] = hashlib.sha256(content).hexdigest()
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.objects / "test-bucket/cases/a-test-case/A Test—Case.md").read_bytes(), content)
        self.assertEqual((self.objects / "test-bucket/cases/manifest.json").read_bytes(),
                         (self.root / "manifest.json").read_bytes())

    def test_failed_republish_leaves_previous_manifest_in_place(self):
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        remote_manifest = self.objects / "test-bucket/cases/manifest.json"
        self.assertTrue(remote_manifest.is_file())
        previous = remote_manifest.read_bytes()
        self.case["title"] = "Updated title"
        self.env["UPLOAD_EXIT"] = "1"
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(remote_manifest.read_bytes(), previous)

    def test_invalid_local_resource_blocks_all_uploads(self):
        (self.root / self.case["introduction"]["path"]).write_bytes(b"corrupt")
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mismatch", result.stderr)
        self.assertEqual(self.uploads(), [])

    def test_duplicate_or_invalid_names_block_uploads(self):
        for cases in [[self.case, self.case], [dict(self.case, name="../outside")]]:
            with self.subTest(cases=cases):
                result = self.run_publish(cases=cases)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.uploads(), [])

    def test_dry_run_validates_without_uploading(self):
        result = self.run_publish("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a-test-case/A Test—Case.md", result.stdout)
        self.assertEqual(self.uploads(), [])

    def test_invalid_s3_target_blocks_uploads(self):
        self.env["AWS_TARGET_FOLDER"] = "missing-bucket/prefix"
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AWS_TARGET_FOLDER", result.stderr)
        self.assertEqual(self.uploads(), [])

    def test_upload_failure_fails_publish_and_stops(self):
        self.env["UPLOAD_EXIT"] = "1"
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.uploads()), 1)


if __name__ == "__main__":
    unittest.main()
