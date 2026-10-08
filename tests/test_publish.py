import hashlib
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
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        aws = bin_dir / "aws"
        aws.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "with open(os.environ['UPLOAD_LOG'], 'a') as f:\n"
            "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "sys.exit(int(os.environ.get('UPLOAD_EXIT', '0')))\n"
        )
        aws.chmod(0o755)
        self.env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                        UPLOAD_LOG=str(self.calls), AWS_TARGET_FOLDER="s3://test-bucket/cases/")
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

    def remote(self, status=200):
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
                    self.send_header("ETag", '"remote-tag"')
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
        })
        for call in self.uploads():
            self.assertEqual(call[:2], ["s3", "cp"])
            self.assertTrue(Path(call[2]).is_file())

    def test_remote_resource_uses_head_even_after_redirect_and_never_uploads(self):
        self.remote()
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests, [("HEAD", "/redirect"), ("HEAD", "/asset")])
        self.assertEqual(len(self.uploads()), 2)
        self.assertFalse(any(call[3].endswith(".science") for call in self.uploads()))
        self.assertIn("681599670", result.stdout)
        self.assertIn("remote-tag", result.stdout)

    def test_head_failure_does_not_fall_back_to_get_or_block_local_uploads(self):
        self.remote(status=405)
        (self.root / self.case["case"]["path"]).unlink()
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests, [("HEAD", "/redirect"), ("HEAD", "/asset")])
        self.assertEqual(len(self.uploads()), 2)
        self.assertIn("HEAD unavailable", result.stdout)
        self.assertIn(self.case["case"]["sha256"], result.stdout)

    def test_missing_optional_introduction_is_allowed(self):
        del self.case["introduction"]
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.uploads()), 2)

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
