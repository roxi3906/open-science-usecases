"""Queue protocol tests: real commit graph, in-memory GitHub API boundary."""
import copy
import sys
from pathlib import Path
from test_publish import GitFixture
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
try:
    import publication_state as state_module
except ImportError:
    state_module = None


class GitHubFixture:
    def __init__(self, baseline, root):
        self.root = root
        self.state = {'version': 1, 'baseline': baseline, 'blocked': None, 'active': None, 'audit_after': 0}
        self.revision = 0
        self.runs = {}
        self.cancelled = []
        self.conflict = False

    def read(self):
        return copy.deepcopy(self.state), self.revision

    def write(self, state, revision):
        if self.conflict or revision != self.revision:
            raise ValueError('State changed concurrently')
        self.state = copy.deepcopy(state); self.revision += 1
        return self.revision

    def run(self, run_id):
        return copy.deepcopy(self.runs[run_id])

    def history(self, since):
        return [copy.deepcopy(r) for r in self.runs.values() if r['run_number'] > since]

    def ancestor(self, before, after):
        return state_module.ancestor(self.root, before, after)

    def cancel(self, run_id):
        self.cancelled.append(run_id)
        self.runs[run_id].update(status='completed', conclusion='cancelled')


class StateTests(GitFixture):
    def setUp(self):
        super().setUp()
        self.api = GitHubFixture(self.base, self.root)
        self.a = self.change()
        self.b = self.change('introduction', '3')
        self.c = self.change('case', '4')

    def run_record(self, number, after, event='push', status='in_progress', conclusion=None):
        run = {'id': number, 'run_number': number, 'head_sha': after, 'head_branch': 'main',
               'event': event, 'status': status, 'conclusion': conclusion, 'run_attempt': 1}
        self.api.runs[number] = run
        return run

    def controller(self, number, after, event='push', attempt=1):
        self.assertIsNotNone(state_module, 'Implement the admission/finalization state protocol')
        run = self.api.runs.get(number) or self.run_record(number, after, event)
        self.git('checkout', '--detach', after)
        return state_module.Controller(self.root, self.api, {**run, 'run_attempt': attempt})

    def admit(self, number, before, after, event='push'):
        ctl = self.controller(number, after, event)
        result = ctl.enter(before=before)
        return ctl, result

    def finish_success(self, ctl, empty=False):
        ctl.finish('success', 'skipped' if empty else 'success', 'false' if empty else 'true')
        self.api.runs[ctl.run['id']].update(status='completed', conclusion='success')

    def test_success_is_promoted_only_after_whole_run_completes(self):
        a, result = self.admit(1, self.base, self.a)
        self.assertTrue(result['proceed'])
        a.finish('success', 'success', 'true')
        self.assertEqual(self.api.state['baseline'], self.base)
        self.api.runs[1].update(status='completed', conclusion='success')
        _, result = self.admit(2, self.a, self.b)
        self.assertTrue(result['proceed'])
        self.assertEqual(self.api.state['baseline'], self.a)

    def test_nonterminal_predecessor_never_waits_under_lock_or_checks_resources(self):
        self.admit(1, self.base, self.a)
        with self.assertRaisesRegex(ValueError, 'workflow_dispatch'):
            self.admit(2, self.a, self.b)
        self.assertEqual(self.api.state['baseline'], self.base)
        self.assertTrue(self.api.state['blocked'])

    def test_failure_cancels_pending_batches_and_future_push_remains_blocked(self):
        for check, publish, has in [('failure', 'skipped', ''), ('success', 'failure', 'true')]:
            with self.subTest(check=check):
                self.api = GitHubFixture(self.base, self.root)
                a, _ = self.admit(1, self.base, self.a)
                self.run_record(2, self.b, status='pending')
                self.run_record(3, self.c, status='queued')
                with self.assertRaises(ValueError):
                    a.finish(check, publish, has)
                self.api.runs[1].update(status='completed', conclusion='failure')
                self.assertEqual(self.api.state['baseline'], self.base)
                self.assertEqual(self.api.cancelled, [2, 3])
                with self.assertRaises(ValueError):
                    self.admit(4, self.c, self.c)
                self.assertTrue(self.api.state['blocked'])

    def test_manual_recovery_merges_net_range_and_new_push_continues_after_success(self):
        a, _ = self.admit(1, self.base, self.a)
        with self.assertRaises(ValueError):
            a.finish('success', 'failure', 'true')
        self.api.runs[1].update(status='completed', conclusion='failure')
        recovery, result = self.admit(2, None, self.b, 'workflow_dispatch')
        self.assertEqual((result['before'], result['after']), (self.base, self.b))
        self.run_record(3, self.c, status='pending')
        self.finish_success(recovery)
        _, following = self.admit(3, self.b, self.c)
        self.assertTrue(following['proceed'])
        self.assertFalse(self.api.state['blocked'])
        self.assertEqual(self.api.state['baseline'], self.b)

    def test_recovery_failure_cancels_new_push_and_keeps_baseline(self):
        recovery, _ = self.admit(1, None, self.b, 'workflow_dispatch')
        self.run_record(2, self.c, status='pending')
        with self.assertRaises(ValueError):
            recovery.finish('failure', 'skipped', '')
        self.assertTrue(self.api.state['blocked'])
        self.assertEqual(self.api.state['baseline'], self.base)
        self.assertEqual(self.api.cancelled, [2])

    def test_old_covered_push_and_older_manual_target_never_republish(self):
        recovery, _ = self.admit(1, None, self.b, 'workflow_dispatch'); self.finish_success(recovery)
        _, covered = self.admit(2, self.base, self.a)
        self.assertFalse(covered['proceed'])
        _, stale_manual = self.admit(3, None, self.a, 'workflow_dispatch')
        self.assertFalse(stale_manual['proceed'])
        self.assertEqual(self.api.state['baseline'], self.b)

    def test_only_explicit_empty_publish_skip_counts_as_success(self):
        a, _ = self.admit(1, self.base, self.a)
        self.finish_success(a, empty=True)
        self.admit(2, self.a, self.b)
        self.assertEqual(self.api.state['baseline'], self.a)
        self.api = GitHubFixture(self.base, self.root)
        a, _ = self.admit(1, self.base, self.a)
        with self.assertRaises(ValueError):
            a.finish('success', 'skipped', 'true')
        self.assertTrue(self.api.state['blocked'])

    def test_cancellation_timeout_interruption_never_promote_even_after_ready(self):
        for conclusion in ('cancelled', 'timed_out', 'failure', 'stale'):
            for ready in (False, True):
                with self.subTest(conclusion=conclusion, ready=ready):
                    self.api = GitHubFixture(self.base, self.root)
                    a, _ = self.admit(1, self.base, self.a)
                    if ready:
                        a.finish('success', 'success', 'true')
                    self.api.runs[1].update(status='completed', conclusion=conclusion)
                    with self.assertRaises(ValueError):
                        self.admit(2, self.a, self.b)
                    self.assertEqual(self.api.state['baseline'], self.base)

    def test_native_rerun_never_reads_or_mutates_state_or_cancels(self):
        a, _ = self.admit(1, self.base, self.a)
        with self.assertRaises(ValueError):
            a.finish('failure', 'skipped', '')
        snapshot = copy.deepcopy(self.api.state); revision = self.api.revision
        rerun = self.controller(1, self.a, attempt=2)
        for method in (lambda: rerun.enter(before=self.base), lambda: rerun.finish('success', 'success', 'true')):
            with self.assertRaisesRegex(ValueError, 'workflow_dispatch'):
                method()
        self.assertEqual((self.api.state, self.api.revision), (snapshot, revision))

    def test_missing_baseline_gap_and_unsupported_history_block(self):
        with self.assertRaises(ValueError):
            self.admit(1, self.a, self.b)  # A has not succeeded.
        self.assertTrue(self.api.state['blocked'])
        self.api = GitHubFixture(None, self.root)
        with self.assertRaises(ValueError):
            self.admit(2, None, self.b, 'workflow_dispatch')
        self.assertIsNone(self.api.state['baseline'])

    def test_cancel_does_not_cancel_manual_or_post_recovery_pushes(self):
        a, _ = self.admit(1, self.base, self.a)
        self.run_record(2, self.b, status='pending')
        self.run_record(3, self.b, 'workflow_dispatch', 'pending')
        self.run_record(4, self.c, status='pending')
        with self.assertRaises(ValueError):
            a.finish('failure', 'skipped', '')
        self.assertEqual(self.api.cancelled, [2])
        self.assertEqual(self.api.runs[3]['status'], 'pending')
        self.assertEqual(self.api.runs[4]['status'], 'pending')

    def test_cancelled_before_admission_is_detected_by_history_audit(self):
        self.run_record(1, self.a, status='completed', conclusion='cancelled')
        with self.assertRaises(ValueError):
            self.admit(2, self.base, self.a)
        self.assertTrue(self.api.state['blocked'])

    def test_cancelled_manual_before_admission_blocks_even_without_commit_gap(self):
        self.run_record(1, self.base, 'workflow_dispatch', 'completed', 'cancelled')
        with self.assertRaises(ValueError):
            self.admit(2, self.base, self.a)
        self.assertTrue(self.api.state['blocked'])

    def test_late_finalizer_cannot_overwrite_recovery_owner(self):
        a, _ = self.admit(1, self.base, self.a)
        self.api.runs[1].update(status='completed', conclusion='cancelled')
        self.admit(2, None, self.b, 'workflow_dispatch')
        snapshot = copy.deepcopy(self.api.state)
        with self.assertRaises(ValueError):
            a.finish('success', 'success', 'true')
        self.assertEqual(self.api.state, snapshot)

    def test_compare_and_swap_conflict_never_admits(self):
        self.api.conflict = True
        with self.assertRaises(ValueError):
            self.admit(1, self.base, self.a)
        self.assertIsNone(self.api.state['active'])

    def test_bootstrap_is_explicit_and_cannot_reset_existing_state(self):
        ctl = self.controller(1, self.b, 'workflow_dispatch')
        with self.assertRaisesRegex(ValueError, 'only allowed'):
            ctl.enter(bootstrap=self.base)
        self.api.state = None
        result = ctl.enter(bootstrap=self.base)
        self.assertEqual((result['before'], result['after']), (self.base, self.b))
        self.assertEqual(self.api.state['baseline'], self.base)

    def test_unavailable_commits_and_divergent_history_do_not_publish(self):
        ctl = self.controller(1, self.b)
        self.api.state['baseline'] = 'f' * 40
        with self.assertRaises(Exception):
            ctl.enter(before=self.a)
        self.assertTrue(self.api.state['blocked'])
        self.api = GitHubFixture(self.base, self.root)
        self.git('checkout', '--orphan', 'other-history')
        (self.root / 'unrelated').write_text('other'); unrelated = self.commit()
        ctl = self.controller(2, unrelated, 'workflow_dispatch')
        with self.assertRaises(ValueError):
            ctl.enter()
        self.assertEqual(self.api.state['baseline'], self.base)

    def test_cancellation_snapshot_race_never_cancels_running_recovery(self):
        a, _ = self.admit(1, self.base, self.a)
        self.run_record(2, self.b, 'workflow_dispatch', 'pending')
        self.run_record(3, self.c, status='pending')
        original = self.api.history
        def racing_history(since):
            snapshot = original(since)
            self.api.runs[2]['status'] = 'in_progress'
            return snapshot
        self.api.history = racing_history
        with self.assertRaises(ValueError):
            a.finish('failure', 'skipped', '')
        self.assertEqual(self.api.cancelled, [])
        self.assertEqual(self.api.state['baseline'], self.base)

    def test_attempt_one_conclusion_remains_authoritative_after_native_rerun(self):
        a, _ = self.admit(1, self.base, self.a)
        a.finish('success', 'success', 'true')
        # The GitHub adapter reads /attempts/1 even if the latest run is now attempt 2.
        self.api.runs[1].update(status='completed', conclusion='cancelled')
        with self.assertRaises(ValueError):
            self.admit(2, self.a, self.b)
        self.assertEqual(self.api.state['baseline'], self.base)

    def test_older_run_number_failure_is_not_erased_by_later_success(self):
        # Run numbers describe creation, not queue admission: recovery B can
        # enter the queue after a later-created push A.
        self.run_record(1, self.b, 'workflow_dispatch', 'pending')
        a, _ = self.admit(2, self.base, self.a)
        self.finish_success(a)
        self.api.runs[1].update(status='completed', conclusion='cancelled')
        with self.assertRaises(ValueError):
            self.admit(3, self.a, self.b)
        self.assertEqual(self.api.state['baseline'], self.a)
        self.assertTrue(self.api.state['blocked'])

    def test_recovery_does_not_erase_older_uncovered_waiter(self):
        self.run_record(1, self.b, 'workflow_dispatch', 'pending')
        recovery, _ = self.admit(2, None, self.a, 'workflow_dispatch')
        self.finish_success(recovery)
        self.api.runs[1].update(status='completed', conclusion='cancelled')
        with self.assertRaises(ValueError):
            self.admit(3, self.a, self.b)
        self.assertEqual(self.api.state['baseline'], self.a)
        self.assertTrue(self.api.state['blocked'])

    def test_retained_uncovered_waiter_does_not_resurrect_covered_manual_failure(self):
        self.run_record(1, self.c, 'workflow_dispatch', 'pending')
        self.run_record(2, self.a, 'workflow_dispatch', 'completed', 'failure')
        recovery, _ = self.admit(3, None, self.b, 'workflow_dispatch')
        self.finish_success(recovery)
        _, result = self.admit(4, self.b, self.c)
        self.assertTrue(result['proceed'])
        self.assertEqual(self.api.state['baseline'], self.b)
        self.assertFalse(self.api.state['blocked'])
