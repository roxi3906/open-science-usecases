"""Exercise run-history HTTP pagination and attempt-one evidence without GitHub."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import unittest
from unittest.mock import patch
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from publication_state import GitHub


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond('GET')
    def do_POST(self):
        self.respond('POST')
    def do_PATCH(self):
        self.respond('PATCH')
    def respond(self, method):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        data = json.loads(body) if body else None
        self.server.calls.append((method, self.path, data))
        status, result = self.server.reply(method, self.path, data)
        self.send_response(status); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps(result).encode())
    def log_message(self, *args):
        pass


class APITests(unittest.TestCase):
    def setUp(self):
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.http.calls = []
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        env = patch.dict(os.environ, {'GITHUB_API_URL': f'http://127.0.0.1:{self.http.server_port}', 'GH_TOKEN': 'test-token'})
        env.start(); self.addCleanup(env.stop)
        self.api = GitHub('owner/repo')

    def test_missing_history_is_an_error_not_an_empty_baseline(self):
        self.http.reply = lambda *_: (404, {})
        with self.assertRaisesRegex(ValueError, '404'):
            self.api.history()
        self.http.reply = lambda *_: (403, {})
        with self.assertRaisesRegex(ValueError, '403'):
            self.api.history()

    def test_jobs_use_attempt_one_and_follow_all_pages(self):
        rows = [{'id': n, 'name': str(n)} for n in range(101)]
        def reply(method, path, body):
            self.assertIn('/attempts/1/jobs?', path)
            page = int(parse_qs(urlsplit(path).query)['page'][0])
            return 200, {'jobs': rows[(page - 1) * 100:page * 100]}
        self.http.reply = reply
        self.assertEqual([job['id'] for job in self.api.jobs(42)], list(range(101)))

    def test_attempt_one_is_used_and_history_paginates_pending_and_cancelled(self):
        rows = [{'id': n, 'run_number': n, 'run_attempt': 1, 'head_branch': 'main', 'path': '.github/workflows/check-structure.yml', 'event': 'push', 'status': 'pending'}
                for n in range(200, 100, -1)]
        rows[0]['run_attempt'] = 2
        def reply(method, path, body):
            if path.endswith('/actions/runs/200/attempts/1'):
                return 200, {**rows[0], 'run_attempt': 1, 'status': 'completed', 'conclusion': 'cancelled'}
            if path.endswith('&page=1'):
                return 200, {'workflow_runs': rows}
            if path.endswith('&page=2'):
                return 200, {'workflow_runs': [{'id': 100, 'run_number': 100, 'run_attempt': 1, 'head_branch': 'main', 'path': '.github/workflows/check-structure.yml', 'event': 'push'}]}
            return 404, {}
        self.http.reply = reply
        runs = self.api.history()
        self.assertEqual(len(runs), 101)
        self.assertEqual(runs[0]['conclusion'], 'cancelled')
        self.assertEqual(runs[-1]['id'], 100)
        self.assertTrue(any('page=2' in path for _, path, _ in self.http.calls))

    def test_history_beyond_1000_filters_branches_and_events_locally(self):
        rows = [{'id': n, 'run_number': n, 'run_attempt': 1, 'head_branch': 'main', 'path': '.github/workflows/check-structure.yml',
                 'event': 'push', 'status': 'completed', 'conclusion': 'cancelled'}
                for n in range(1105, 0, -1)]
        rows[0]['head_branch'] = 'feature'
        rows[1]['event'] = 'pull_request'
        def reply(method, path, body):
            query = parse_qs(urlsplit(path).query)
            page = int(query['page'][0])
            # Model GitHub's documented filtered-query cap at the HTTP boundary.
            selected = rows[:1000] if 'branch' in query else rows
            return 200, {'workflow_runs': selected[(page - 1) * 100:page * 100]}
        self.http.reply = reply
        runs = self.api.history()
        self.assertEqual([run['id'] for run in runs], list(range(1103, 0, -1)))
        self.assertTrue(any(parse_qs(urlsplit(path).query)['page'] == ['12']
                            for _, path, _ in self.http.calls))

    def test_history_includes_legacy_publish_but_ignores_unrelated_workflows(self):
        rows = [
            {'id': 1, 'run_attempt': 1, 'head_branch': 'main', 'event': 'workflow_run',
             'path': '.github/workflows/publish.yml'},
            {'id': 2, 'run_attempt': 1, 'head_branch': 'main', 'event': 'push',
             'path': '.github/workflows/unrelated.yml'},
            {'id': 3, 'run_attempt': 1, 'head_branch': 'main', 'event': 'pull_request',
             'path': '.github/workflows/check-structure.yml'},
        ]
        self.http.reply = lambda *_: (200, {'workflow_runs': rows})
        self.assertEqual([run['id'] for run in self.api.history()], [1])
        self.assertIn('/actions/runs?', self.http.calls[0][1])

    def test_history_repeated_page_is_an_explicit_error(self):
        rows = [{'id': n, 'run_number': n, 'run_attempt': 1, 'head_branch': 'main', 'path': '.github/workflows/check-structure.yml', 'event': 'push'}
                for n in range(1100, 1000, -1)]
        self.http.reply = lambda *_: (200, {'workflow_runs': rows})
        with self.assertRaisesRegex(ValueError, 'did not advance'):
            self.api.history()

    def test_history_deduplicates_overlapping_pages_when_new_runs_arrive(self):
        rows = [{'id': n, 'run_number': n, 'run_attempt': 1, 'head_branch': 'main', 'path': '.github/workflows/check-structure.yml', 'event': 'push'}
                for n in range(200, 0, -1)]
        def reply(method, path, body):
            page = int(parse_qs(urlsplit(path).query)['page'][0])
            # A new run inserted at the front shifts the next page by one row.
            offset = 0 if page == 1 else (page - 1) * 100 - 1
            return 200, {'workflow_runs': rows[offset:offset + 100]}
        self.http.reply = reply
        self.assertEqual([run['id'] for run in self.api.history()], list(range(200, 0, -1)))

    def test_history_second_page_failure_does_not_return_partial_results(self):
        rows = [{'id': n, 'run_number': n, 'run_attempt': 1, 'head_branch': 'main', 'path': '.github/workflows/check-structure.yml', 'event': 'push'}
                for n in range(200, 100, -1)]
        self.http.reply = lambda method, path, body: ((200, {'workflow_runs': rows})
                                                     if path.endswith('&page=1') else (503, {}))
        with self.assertRaisesRegex(ValueError, 'HTTP 503'):
            self.api.history()

    def test_queued_target_ancestry_uses_github_metadata_not_stale_local_fetch(self):
        self.http.reply = lambda *_: (200, {'status': 'ahead'})
        self.assertTrue(self.api.ancestor('a' * 40, 'b' * 40))
        self.assertTrue(self.http.calls[-1][1].endswith('/compare/' + 'a' * 40 + '...' + 'b' * 40))
        self.http.reply = lambda *_: (200, {'status': 'diverged'})
        self.assertFalse(self.api.ancestor('a' * 40, 'b' * 40))
