"""History-based admission against real Git graphs and an in-memory API boundary."""
import copy
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path
from test_publish import GitFixture
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import publication_state as publication


def timestamp(number):
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=number)).isoformat()


class GitHubFixture:
    def __init__(self, root):
        self.root, self.runs, self.jobs_by_run, self.logs = root, {}, {}, {}
        self.cancelled = []

    def read(self):
        raise AssertionError('Admission must not read a state branch')

    def write(self, *args):
        raise AssertionError('Admission must not write a state branch')

    def run(self, run_id):
        return copy.deepcopy(self.runs[run_id])

    def history(self):
        return copy.deepcopy(list(self.runs.values()))

    def jobs(self, run_id):
        return copy.deepcopy(self.jobs_by_run.get(run_id, []))

    def job_log(self, job_id):
        return self.logs[job_id]

    def ancestor(self, before, after):
        return publication.ancestor(self.root, before, after)

    def cancel(self, run_id):
        self.cancelled.append(run_id)
        self.runs[run_id].update(status='completed', conclusion='cancelled')


class HistoryTests(GitFixture):
    def setUp(self):
        super().setUp()
        self.api = GitHubFixture(self.root)
        self.a = self.change()
        self.b = self.change('introduction', '3')
        self.c = self.change('case', '4')
        self.record(1, self.base, 'workflow_run', 'completed', 'success')
        self.api.jobs_by_run[1] = [self.job(1, 'publish', {
            'Check out the validated commit': 'success',
            'Publish local resources to S3': 'success',
        })]
        self.api.logs[1] = ('2026-01-01T00:00:00Z [command]/usr/bin/git log -1 --format=%H\n'
                            '2026-01-01T00:00:01Z ' + self.base + '\n')

    def job(self, number, name, steps, conclusion='success'):
        return {'id': number, 'name': name, 'status': 'completed', 'conclusion': conclusion,
                'started_at': timestamp(number - 0.5), 'completed_at': timestamp(number),
                'steps': [{'name': name, 'conclusion': result} for name, result in steps.items()]}

    def record(self, number, sha, event='push', status='in_progress', conclusion=None):
        run = {'id': number, 'run_number': number, 'run_attempt': 1, 'head_sha': sha,
               'head_branch': 'main', 'event': event, 'status': status, 'conclusion': conclusion,
               'created_at': timestamp(number),
               'path': '.github/workflows/' + ('publish.yml' if event == 'workflow_run' else 'check-structure.yml')}
        self.api.runs[number] = run
        return run

    def successful(self, number, sha, event='push', empty=False):
        run = self.record(number, sha, event, 'completed', 'success')
        self.api.jobs_by_run[number] = [
            self.job(number, 'check-structure', {
                'Admit this batch under the shared serial lock': 'success',
                'Prepare uploads from the checked manifest difference': 'success',
            }),
            self.job(number, 'publish', {'Upload resources, then manifest': 'success'},
                     'skipped' if empty else 'success'),
            self.job(number, 'finalize', {'Validate publication result and cancel waiters': 'success'}),
        ]
        return run

    def controller(self, number, sha, event='push'):
        run = self.api.runs.get(number) or self.record(number, sha, event)
        self.git('checkout', '--detach', sha)
        return publication.Controller(self.root, self.api, copy.deepcopy(run))

    def admit(self, number, before, after, event='push'):
        return self.controller(number, after, event).enter(before=before)

    def test_legacy_actual_upload_initializes_from_history_without_state(self):
        result = self.admit(2, self.base, self.a)
        self.assertEqual(result, {'proceed': True, 'before': self.base, 'after': self.a})

    def test_legacy_target_comes_from_checkout_not_workflow_run_head_sha(self):
        self.api.runs[1]['head_sha'] = self.c
        result = self.admit(2, None, self.b, 'workflow_dispatch')
        self.assertEqual(result['before'], self.base)

    def test_green_legacy_skip_is_not_a_baseline(self):
        self.api.jobs_by_run[1][0]['steps'][-1]['conclusion'] = 'skipped'
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.admit(2, None, self.b, 'workflow_dispatch')

    def test_missing_or_ambiguous_legacy_checkout_log_blocks(self):
        for log in ('expired logs', self.api.logs[1] + self.api.logs[1].replace(self.base, self.a)):
            with self.subTest(log=log):
                self.api.logs[1] = log
                with self.assertRaisesRegex(ValueError, 'checkout'):
                    self.admit(2, None, self.b, 'workflow_dispatch')

    def legacy_sync(self, number, sha, start, end):
        run = self.record(number, sha, 'workflow_run', 'completed', 'success')
        job = self.job(number, 'publish', {
            'Check out the validated commit': 'success',
            'Publish local resources to S3': 'success',
        })
        job.update(started_at=timestamp(start), completed_at=timestamp(end))
        self.api.jobs_by_run[number] = [job]
        self.api.logs[number] = ('2026-01-01T00:00:00Z [command]/usr/bin/git log -1 --format=%H\n'
                                 '2026-01-01T00:00:01Z ' + sha + '\n')
        return run

    def missing_log(self, job_id):
        original = self.api.job_log
        def read(number):
            if number == job_id:
                raise ValueError('HTTP 404: required legacy log unavailable')
            return original(number)
        self.api.job_log = read

    def test_latest_full_sync_covers_missing_older_log(self):
        self.legacy_sync(2, self.b, 3, 4)
        self.missing_log(1)
        result = self.admit(5, None, self.c, 'workflow_dispatch')
        self.assertEqual((result['before'], result['after']), (self.b, self.c))

    def test_legacy_coverage_uses_completion_not_creation_or_listing_order(self):
        # The older-created run waits longer and performs the last complete sync.
        self.legacy_sync(1, self.b, 5, 6)
        self.legacy_sync(2, self.a, 3, 4)
        self.missing_log(2)
        self.api.runs = dict(reversed(list(self.api.runs.items())))
        self.assertEqual(self.admit(7, None, self.c, 'workflow_dispatch')['before'], self.b)

    def test_later_finishing_sync_with_missing_log_is_still_required(self):
        self.legacy_sync(1, self.base, 5, 6)
        self.legacy_sync(2, self.b, 3, 4)
        self.missing_log(1)
        with self.assertRaisesRegex(ValueError, 'required legacy log'):
            self.admit(7, None, self.c, 'workflow_dispatch')

    def test_older_log_outside_verified_commit_coverage_is_still_required(self):
        self.legacy_sync(1, self.c, 1, 2)
        self.legacy_sync(2, self.b, 3, 4)
        self.missing_log(1)
        with self.assertRaisesRegex(ValueError, 'required legacy log'):
            self.admit(5, None, self.c, 'workflow_dispatch')

    def test_legacy_coverage_uses_verified_checkout_not_newer_workflow_head(self):
        self.legacy_sync(1, self.b, 1, 2)
        newest = self.legacy_sync(2, self.a, 3, 4)
        newest['head_sha'] = self.c  # The run metadata must not widen actual sync coverage.
        self.missing_log(1)
        with self.assertRaisesRegex(ValueError, 'required legacy log'):
            self.admit(5, None, self.c, 'workflow_dispatch')

    def test_overlapping_or_tied_syncs_cannot_hide_a_missing_log(self):
        for start, end in ((3, 6), (1, 5), (1, 3)):
            with self.subTest(start=start, end=end):
                self.legacy_sync(1, self.base, start, end)
                self.legacy_sync(2, self.b, 3, 5)
                self.missing_log(1)
                with self.assertRaisesRegex(ValueError, 'required legacy log'):
                    self.admit(7, None, self.c, 'workflow_dispatch')

    def test_missing_legacy_completion_evidence_blocks(self):
        self.legacy_sync(2, self.b, 3, 4)
        del self.api.jobs_by_run[1][0]['completed_at']
        with self.assertRaisesRegex(ValueError, 'timing evidence'):
            self.admit(5, None, self.c, 'workflow_dispatch')

    def test_skipped_older_log_does_not_resurrect_a_covered_failure(self):
        self.legacy_sync(1, self.b, 5, 6)
        self.record(2, self.b, 'workflow_run', 'completed', 'failure')
        self.api.jobs_by_run[2] = [self.job(2, 'publish', {}, 'failure')]
        self.legacy_sync(3, self.b, 3, 4)
        self.missing_log(3)
        self.assertTrue(self.admit(7, self.b, self.c)['proceed'])

    def test_late_failure_is_not_cleared_by_an_earlier_full_sync(self):
        for sync_id in (1, 3):
            with self.subTest(sync_id=sync_id):
                self.legacy_sync(sync_id, self.b, 5, 6)
                self.record(2, self.b, 'workflow_run', 'completed', 'failure')
                job = self.job(2, 'publish', {}, 'failure')
                job.update(started_at=timestamp(6), completed_at=timestamp(7))
                self.api.jobs_by_run[2] = [job]
                with self.assertRaisesRegex(ValueError, 'workflow_dispatch'):
                    self.admit(8, self.b, self.c)

    def test_all_failed_run_jobs_must_finish_before_the_covering_sync(self):
        self.legacy_sync(1, self.b, 5, 6)
        self.record(2, self.b, 'workflow_run', 'completed', 'failure')
        self.api.jobs_by_run[2] = [self.job(2, 'publish', {}, 'failure'),
                                   self.job(7, 'cleanup', {}, 'failure')]
        with self.assertRaisesRegex(ValueError, 'workflow_dispatch'):
            self.admit(8, self.b, self.c)

    def test_covered_failure_still_requires_its_completion_evidence(self):
        self.legacy_sync(1, self.b, 5, 6)
        self.record(2, self.b, 'workflow_run', 'completed', 'failure')
        job = self.job(2, 'publish', {}, 'failure')
        job['completed_at'] = None
        self.api.jobs_by_run[2] = [job]
        with self.assertRaisesRegex(ValueError, 'timing evidence'):
            self.admit(7, self.b, self.c)

    def test_check_only_success_does_not_advance_baseline(self):
        self.record(2, self.a, status='completed', conclusion='success')
        self.api.jobs_by_run[2] = [self.job(2, 'check-structure', {'Check names': 'success'})]
        result = self.admit(3, None, self.b, 'workflow_dispatch')
        self.assertEqual(result['before'], self.base)

    def test_completed_publication_advances_baseline_including_empty_plan(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                self.successful(2, self.a, empty=empty)
                result = self.admit(3, self.a, self.b)
                self.assertEqual(result['before'], self.a)

    def test_successful_check_cannot_cover_failed_or_missing_finalization(self):
        self.successful(2, self.a)
        self.api.jobs_by_run[2][-1]['conclusion'] = 'skipped'
        with self.assertRaisesRegex(ValueError, 'publication'):
            self.admit(3, self.a, self.b)

    def test_nonterminal_predecessor_does_not_advance_baseline_or_wait(self):
        self.successful(2, self.a)
        self.api.runs[2].update(status='in_progress', conclusion=None)
        with self.assertRaisesRegex(ValueError, 'terminal'):
            self.admit(3, self.a, self.b)

    def test_failed_cancelled_and_timed_out_runs_block_future_pushes(self):
        for conclusion in ('failure', 'cancelled', 'timed_out', 'stale'):
            with self.subTest(conclusion=conclusion):
                self.record(2, self.a, status='completed', conclusion=conclusion)
                with self.assertRaisesRegex(ValueError, 'workflow_dispatch'):
                    self.admit(3, self.a, self.b)

    def test_failure_even_at_baseline_needs_manual_recovery(self):
        self.record(2, self.base, 'workflow_dispatch', 'completed', 'cancelled')
        with self.assertRaisesRegex(ValueError, 'workflow_dispatch'):
            self.admit(3, self.base, self.a)

    def test_manual_recovery_uses_last_success_and_unblocks_after_terminal_success(self):
        self.record(2, self.a, status='completed', conclusion='failure')
        result = self.admit(3, None, self.b, 'workflow_dispatch')
        self.assertEqual((result['before'], result['after']), (self.base, self.b))
        self.successful(3, self.b, 'workflow_dispatch')
        self.assertTrue(self.admit(4, self.b, self.c)['proceed'])

    def test_recovery_does_not_cover_older_failure_beyond_its_target(self):
        self.record(2, self.c, 'workflow_dispatch', 'completed', 'cancelled')
        self.successful(3, self.b, 'workflow_dispatch')
        with self.assertRaisesRegex(ValueError, 'workflow_dispatch'):
            self.admit(4, self.b, self.c)

    def test_older_failure_is_not_erased_by_later_automatic_success(self):
        self.record(2, self.b, 'workflow_dispatch', 'completed', 'failure')
        self.successful(3, self.a)
        with self.assertRaisesRegex(ValueError, 'workflow_dispatch'):
            self.admit(4, self.a, self.b)

    def test_failure_after_recovery_is_not_hidden_by_same_commit(self):
        self.successful(2, self.b, 'workflow_dispatch')
        self.record(3, self.b, 'workflow_dispatch', 'completed', 'failure')
        with self.assertRaisesRegex(ValueError, 'workflow_dispatch'):
            self.admit(4, self.b, self.c)

    def test_covered_failure_does_not_reappear_behind_uncovered_waiter(self):
        self.record(2, self.c, 'workflow_dispatch', 'pending')
        self.record(3, self.a, 'workflow_dispatch', 'completed', 'failure')
        self.successful(4, self.b, 'workflow_dispatch')
        self.assertTrue(self.admit(5, self.b, self.c)['proceed'])

    def test_covered_push_skips_and_stale_manual_cannot_roll_back(self):
        self.successful(2, self.b, 'workflow_dispatch')
        self.assertFalse(self.admit(3, self.base, self.a)['proceed'])
        with self.assertRaisesRegex(ValueError, 'older'):
            self.admit(4, None, self.a, 'workflow_dispatch')

    def test_baseline_follows_ancestry_not_run_creation_order(self):
        self.successful(2, self.b, 'workflow_dispatch')
        self.successful(3, self.a)
        self.assertEqual(self.admit(4, None, self.c, 'workflow_dispatch')['before'], self.b)

    def test_missing_predecessor_requires_manual_recovery(self):
        with self.assertRaisesRegex(ValueError, 'predecessor'):
            self.admit(2, self.a, self.b)

    def test_first_ever_sync_still_requires_explicit_known_baseline(self):
        self.api.runs.clear()
        ctl = self.controller(2, self.b, 'workflow_dispatch')
        with self.assertRaisesRegex(ValueError, 'baseline'):
            ctl.enter()
        self.assertEqual(ctl.enter(bootstrap=self.base)['before'], self.base)

    def test_explicit_baseline_cannot_override_successful_history(self):
        with self.assertRaisesRegex(ValueError, 'initial_baseline'):
            self.controller(2, self.b, 'workflow_dispatch').enter(bootstrap=self.a)

    def test_native_rerun_refuses_before_reading_history_or_cancelling(self):
        ctl = self.controller(2, self.a)
        ctl.run['run_attempt'] = 2
        self.api.history = lambda: self.fail('Rerun must not access history')
        for call in (lambda: ctl.enter(before=self.base), lambda: ctl.finish('success', 'success', 'true')):
            with self.assertRaisesRegex(ValueError, 'reruns'):
                call()
        self.assertEqual(self.api.cancelled, [])

    def test_failure_cancels_push_waiters_but_preserves_manual_barrier_and_later_targets(self):
        ctl = self.controller(2, self.a)
        self.record(3, self.b, status='pending')
        self.record(4, self.b, 'workflow_dispatch', 'pending')
        self.record(5, self.c, status='pending')
        with self.assertRaises(ValueError):
            ctl.finish('success', 'failure', 'true')
        self.assertEqual(self.api.cancelled, [3])
        self.api.runs[2].update(status='completed', conclusion='failure')
        with self.assertRaises(ValueError):
            self.admit(6, self.b, self.c)

    def test_finalizer_rejects_unexplained_publish_skip(self):
        ctl = self.controller(2, self.a)
        with self.assertRaises(ValueError):
            ctl.finish('success', 'skipped', 'true')
        ctl.finish('success', 'skipped', 'false')

    def test_late_finalizer_cannot_cancel_waiters_after_its_run_ended(self):
        ctl = self.controller(2, self.a)
        self.api.runs[2].update(status='completed', conclusion='cancelled')
        self.record(3, self.b, status='pending')
        with self.assertRaises(ValueError):
            ctl.finish('failure', 'skipped', '')
        self.assertEqual(self.api.cancelled, [])

    def test_history_failure_never_falls_back_to_caller_baseline(self):
        def unavailable():
            raise ValueError('GitHub unavailable')
        self.api.history = unavailable
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.controller(2, self.b, 'workflow_dispatch').enter(bootstrap=self.base)

    def test_recovery_over_1000_failures_does_not_use_remote_ancestry_for_local_commits(self):
        for number in range(2, 1003):
            self.record(number, self.a, status='completed', conclusion='cancelled')
        self.successful(1003, self.b, 'workflow_dispatch')
        self.api.ancestor = lambda *_: self.fail('Use locally available Git objects')
        self.assertTrue(self.admit(1004, self.b, self.c)['proceed'])

    def test_late_cancelled_push_already_covered_by_recovery_does_not_block(self):
        self.successful(2, self.b, 'workflow_dispatch')
        self.record(3, self.a, status='completed', conclusion='cancelled')
        self.assertTrue(self.admit(4, self.b, self.c)['proceed'])

    def test_long_successful_history_does_not_exhaust_job_api_budget(self):
        for number in range(2, 1003):
            self.successful(number, self.b, 'workflow_dispatch')
        original = self.api.jobs
        calls = []
        def limited_jobs(run_id):
            calls.append(run_id)
            if len(calls) > 10:
                raise ValueError('Job API budget exhausted')
            return original(run_id)
        self.api.jobs = limited_jobs
        self.assertEqual(self.admit(1003, None, self.c, 'workflow_dispatch')['before'], self.b)

    def test_missing_successful_run_jobs_is_not_treated_as_check_only(self):
        self.record(2, self.a, status='completed', conclusion='success')
        with self.assertRaisesRegex(ValueError, 'evidence'):
            self.admit(3, None, self.b, 'workflow_dispatch')

    def test_extracted_package_publication_advances_baseline(self):
        self.successful(2, self.a)
        self.api.jobs_by_run[2][1]['steps'] = [
            {'name': 'Download and extract changed science packages', 'conclusion': 'success'},
            {'name': 'Replace extracted content and upload resources, then manifest', 'conclusion': 'success'},
        ]
        self.assertEqual(self.admit(3, self.a, self.b)['before'], self.a)

    def test_extracted_publisher_needs_successful_preparation_evidence(self):
        self.successful(2, self.a)
        self.api.jobs_by_run[2][1]['steps'] = [
            {'name': 'Download and extract changed science packages', 'conclusion': 'skipped'},
            {'name': 'Replace extracted content and upload resources, then manifest', 'conclusion': 'success'},
        ]
        with self.assertRaisesRegex(ValueError, 'publication'):
            self.admit(3, self.a, self.b)


class CLIIntegrationTests(GitFixture):
    """Run the Actions entrypoint against an HTTP server and a temporary checkout."""
    def setUp(self):
        super().setUp()
        import os
        import threading
        from http.server import ThreadingHTTPServer
        from test_github_state_api import Handler
        self.after = self.change()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.http.calls = []
        thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.run = {'id': 2, 'run_attempt': 1, 'head_sha': self.after, 'head_branch': 'main',
                    'event': 'push', 'status': 'in_progress', 'conclusion': None,
                    'created_at': timestamp(2), 'path': '.github/workflows/check-structure.yml'}
        self.previous = {**self.run, 'id': 1, 'head_sha': self.base, 'event': 'workflow_dispatch',
                         'status': 'completed', 'conclusion': 'success', 'created_at': timestamp(1)}
        jobs = []
        for name, step_names in (
            ('check-structure', ['Admit this batch under the shared serial lock',
                                 'Prepare uploads from the checked manifest difference']),
            ('publish', ['Upload resources, then manifest']),
            ('finalize', ['Validate publication result and cancel waiters']),
        ):
            jobs.append({'id': len(jobs) + 1, 'name': name, 'status': 'completed', 'conclusion': 'success',
                         'steps': [{'name': step, 'conclusion': 'success'} for step in step_names]})
        def reply(method, path, body):
            if method != 'GET':
                return 403, {'message': 'No writes authorized in this fixture'}
            if '/actions/runs/2/attempts/1' in path:
                return 200, self.run
            if '/actions/runs/1/attempts/1/jobs?' in path:
                return 200, {'jobs': jobs}
            if '/actions/runs?' in path:
                return 200, {'workflow_runs': [self.run, self.previous]}
            return 404, {}
        self.http.reply = reply
        event = self.root / 'event.json'
        import json
        event.write_text(json.dumps({'before': self.base, 'after': self.after}))
        self.output = self.root / 'outputs.txt'
        self.env = {**os.environ, 'GITHUB_API_URL': f'http://127.0.0.1:{self.http.server_port}',
                    'GH_TOKEN': 'test-token', 'GITHUB_REPOSITORY': 'owner/repo',
                    'GITHUB_RUN_ID': '2', 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_SHA': self.after,
                    'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_PATH': str(event),
                    'GITHUB_OUTPUT': str(self.output), 'INITIAL_BASELINE': ''}

    def invoke(self, command):
        import subprocess
        return subprocess.run([sys.executable, '-B', publication.__file__, command], cwd=self.root,
                              env=self.env, capture_output=True, text=True)

    def test_entrypoint_exports_fixed_range_and_never_writes_repository_state(self):
        result = self.invoke('enter')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text().splitlines(),
                         ['proceed=true', 'before=' + self.base, 'after=' + self.after])
        self.assertTrue(all(method == 'GET' and '/actions/' in path
                            for method, path, _ in self.http.calls))

    def test_finalizer_accepts_explicit_covered_skip_without_advancing_history(self):
        import json
        self.env['BATCH_RESULTS'] = json.dumps({
            'check-structure': {'result': 'success', 'outputs': {'proceed': 'false'}},
            'publish': {'result': 'skipped'},
        })
        result = self.invoke('finish')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.output.exists())
        self.assertTrue(all(method == 'GET' for method, _, _ in self.http.calls))

    def test_entrypoint_rejects_moving_target_and_rerun_without_outputs(self):
        self.env['GITHUB_SHA'] = self.base
        result = self.invoke('enter')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('fixed event SHA', result.stderr)
        self.assertFalse(self.output.exists())
        self.http.calls.clear()
        self.env['GITHUB_RUN_ATTEMPT'] = '2'
        result = self.invoke('enter')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.http.calls, [])
