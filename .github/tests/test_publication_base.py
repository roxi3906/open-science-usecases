import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publication_base.py"
REPO = "owner/cases"
SHA = "a" * 40


class PublicationBaseTests(unittest.TestCase):
    def run_base(self, runs, jobs, attempt="1"):
        # Fake only GitHub's network boundary; execute the real CLI and planner.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            responses = {"repos/" + REPO + "/actions/workflows/publish.yml/runs?per_page=100":
                         [{"workflow_runs": runs[:1]}, {"workflow_runs": runs[1:]}]}
            for run_id, value in jobs.items():
                responses["repos/" + REPO + "/actions/runs/" + str(run_id) +
                          "/jobs?filter=all&per_page=100"] = [{"jobs": value}]
            (root / "responses.json").write_text(json.dumps(responses))
            gh = root / "gh"
            gh.write_text("#!" + sys.executable + "\n"
                          "import json, os, sys\n"
                          "assert sys.argv[1:4] == ['api', '--paginate', '--slurp']\n"
                          "data = json.load(open(os.environ['RESPONSES']))\n"
                          "print(json.dumps(data[sys.argv[4]]))\n")
            gh.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                       RESPONSES=str(root / "responses.json"), GITHUB_REPOSITORY=REPO,
                       GITHUB_RUN_ID="99", GITHUB_RUN_ATTEMPT=attempt,
                       GITHUB_OUTPUT=str(root / "output"))
            result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return (root / "output").read_text()

    def run_record(self, run_id, title=None, updated="2026-10-08T10:00:00Z"):
        return {"id": run_id, "display_title": title or "Publish " + SHA,
                "updated_at": updated, "event": "workflow_run", "head_branch": "main",
                "head_repository": {"full_name": REPO}, "head_sha": "b" * 40}

    def job(self, conclusion="success", completed="2026-10-08T09:59:00Z", job_id=1):
        return {"id": job_id, "name": "publish", "steps": [
            {"name": "Publish local resources to S3", "conclusion": conclusion,
             "status": "completed", "started_at": "2026-10-08T09:58:00Z",
             "completed_at": completed},
        ]}

    def test_uses_validated_sha_not_workflow_run_head_sha(self):
        output = self.run_base([self.run_record(1)], {1: [self.job()]})
        self.assertEqual(output, "base=" + SHA + "\n")

    def test_skipped_publish_does_not_become_baseline_and_pages_are_searched(self):
        output = self.run_base([self.run_record(2), self.run_record(1)],
                               {2: [self.job("skipped")], 1: [self.job()]})
        self.assertEqual(output, "base=" + SHA + "\n")

    def test_partial_failure_requires_full_recovery_even_if_changes_were_reverted(self):
        for conclusion in ("failure", "cancelled", "timed_out"):
            with self.subTest(conclusion=conclusion):
                output = self.run_base([self.run_record(2), self.run_record(1)],
                                       {2: [self.job(conclusion, job_id=2)], 1: [self.job()]})
                self.assertEqual(output, "base=\n")

    def test_orders_publications_by_actual_completion_including_rerun_attempts(self):
        output = self.run_base([self.run_record(2), self.run_record(1)], {
            2: [self.job("failure", "2026-10-08T09:55:00Z", 2)],
            1: [self.job("failure", "2026-10-08T09:50:00Z"), self.job(job_id=3)],
        })
        self.assertEqual(output, "base=" + SHA + "\n")

    def test_legacy_runs_without_exact_checked_sha_require_initial_full_publish(self):
        output = self.run_base([self.run_record(1, title="publish")], {1: [self.job()]})
        self.assertEqual(output, "base=\n")

    def test_no_history_and_reruns_use_full_publish(self):
        self.assertEqual(self.run_base([], {}), "base=\n")
        self.assertEqual(self.run_base([], {}, attempt="2"), "base=\n")

    def test_other_repositories_and_non_main_runs_cannot_supply_baseline(self):
        foreign = self.run_record(1)
        foreign["head_repository"]["full_name"] = "other/repo"
        branch = self.run_record(2)
        branch["head_branch"] = "feature"
        self.assertEqual(self.run_base([foreign, branch, self.run_record(99)], {}), "base=\n")


if __name__ == "__main__":
    unittest.main()
