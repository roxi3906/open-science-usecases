"""Exercise the HTTP boundary and CAS payloads without accessing GitHub."""
import base64
import copy
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
        self.api = GitHub('owner/repo', 'check-structure.yml')

    def test_state_read_and_fast_forward_write_compare_and_swap(self):
        state = {'baseline': 'a' * 40}
        def reply(method, path, body):
            suffix = path.removeprefix('/repos/owner/repo/')
            if suffix == 'git/ref/heads/check-publish-state':
                return 200, {'object': {'sha': 'old-state'}}
            if suffix == 'contents/state.json?ref=old-state':
                return 200, {'encoding': 'base64', 'content': base64.b64encode(json.dumps(state).encode()).decode()}
            if suffix == 'git/trees':
                return 201, {'sha': 'tree'}
            if suffix == 'git/commits':
                return 201, {'sha': 'new-state'}
            if suffix == 'git/refs/heads/check-publish-state':
                return 200, {'object': {'sha': 'new-state'}}
            return 404, {}
        self.http.reply = reply
        loaded, revision = self.api.read()
        self.assertEqual((loaded, revision), (state, 'old-state'))
        self.assertEqual(self.api.write(state, revision), 'new-state')
        commit = next(body for method, path, body in self.http.calls if path.endswith('/git/commits'))
        self.assertEqual(commit['parents'], ['old-state'])
        self.assertEqual(self.http.calls[-1][2], {'sha': 'new-state', 'force': False})
        # A conflicting ref update fails; it never retries with force=true.
        self.http.reply = lambda method, path, body: (422, {}) if method == 'PATCH' else reply(method, path, body)
        with self.assertRaisesRegex(ValueError, 'HTTP 422'):
            self.api.write(state, revision)
        self.assertEqual(self.http.calls[-1][2]['force'], False)

    def test_missing_state_only_404_is_absence(self):
        self.http.reply = lambda *_: (404, {})
        self.assertEqual(self.api.read(), (None, None))
        self.http.reply = lambda *_: (403, {})
        with self.assertRaisesRegex(ValueError, '403'):
            self.api.read()

    def test_attempt_one_is_used_and_history_paginates_pending_and_cancelled(self):
        rows = [{'id': n, 'run_number': n, 'run_attempt': 1, 'head_branch': 'main', 'event': 'push', 'status': 'pending'}
                for n in range(200, 100, -1)]
        rows[0]['run_attempt'] = 2
        def reply(method, path, body):
            if path.endswith('/actions/runs/200/attempts/1'):
                return 200, {**rows[0], 'run_attempt': 1, 'status': 'completed', 'conclusion': 'cancelled'}
            if path.endswith('&page=1'):
                return 200, {'workflow_runs': rows}
            if path.endswith('&page=2'):
                return 200, {'workflow_runs': [{'id': 100, 'run_number': 100, 'run_attempt': 1, 'head_branch': 'main', 'event': 'push'}]}
            return 404, {}
        self.http.reply = reply
        runs = self.api.history(100)
        self.assertEqual(len(runs), 100)
        self.assertEqual(runs[0]['conclusion'], 'cancelled')
        self.assertEqual(runs[-1]['id'], 101)
        self.assertTrue(any('page=2' in path for _, path, _ in self.http.calls))

    def test_history_beyond_1000_filters_branches_and_events_locally(self):
        rows = [{'id': n, 'run_number': n, 'run_attempt': 1, 'head_branch': 'main',
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
        runs = self.api.history(1)
        self.assertEqual([run['id'] for run in runs], list(range(1103, 1, -1)))
        self.assertTrue(any(parse_qs(urlsplit(path).query)['page'] == ['12']
                            for _, path, _ in self.http.calls))

    def test_history_stops_at_audit_boundary_without_scanning_older_pages(self):
        rows = [{'id': n, 'run_number': n, 'run_attempt': 1, 'head_branch': 'main', 'event': 'push'}
                for n in range(1100, 1000, -1)]
        self.http.reply = lambda *_: (200, {'workflow_runs': rows})
        runs = self.api.history(1098)
        self.assertEqual([run['id'] for run in runs], [1100, 1099])
        self.assertEqual(len(self.http.calls), 1)

    def test_history_repeated_page_is_an_explicit_error(self):
        rows = [{'id': n, 'run_number': n, 'run_attempt': 1, 'head_branch': 'main', 'event': 'push'}
                for n in range(1100, 1000, -1)]
        self.http.reply = lambda *_: (200, {'workflow_runs': rows})
        with self.assertRaisesRegex(ValueError, 'did not advance'):
            self.api.history(1)

    def test_history_deduplicates_overlapping_pages_when_new_runs_arrive(self):
        rows = [{'id': n, 'run_number': n, 'run_attempt': 1, 'head_branch': 'main', 'event': 'push'}
                for n in range(200, 0, -1)]
        def reply(method, path, body):
            page = int(parse_qs(urlsplit(path).query)['page'][0])
            # A new run inserted at the front shifts the next page by one row.
            offset = 0 if page == 1 else (page - 1) * 100 - 1
            return 200, {'workflow_runs': rows[offset:offset + 100]}
        self.http.reply = reply
        self.assertEqual([run['id'] for run in self.api.history(1)], list(range(200, 1, -1)))

    def test_history_second_page_failure_does_not_return_partial_results(self):
        rows = [{'id': n, 'run_number': n, 'run_attempt': 1, 'head_branch': 'main', 'event': 'push'}
                for n in range(200, 100, -1)]
        self.http.reply = lambda method, path, body: ((200, {'workflow_runs': rows})
                                                     if path.endswith('&page=1') else (503, {}))
        with self.assertRaisesRegex(ValueError, 'HTTP 503'):
            self.api.history(1)

    def test_queued_target_ancestry_uses_github_metadata_not_stale_local_fetch(self):
        self.http.reply = lambda *_: (200, {'status': 'ahead'})
        self.assertTrue(self.api.ancestor('a' * 40, 'b' * 40))
        self.assertTrue(self.http.calls[-1][1].endswith('/compare/' + 'a' * 40 + '...' + 'b' * 40))
        self.http.reply = lambda *_: (200, {'status': 'diverged'})
        self.assertFalse(self.api.ancestor('a' * 40, 'b' * 40))
