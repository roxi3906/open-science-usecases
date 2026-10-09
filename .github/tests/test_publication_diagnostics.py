"""Execute the workflow's diagnostic shell without GitHub or S3 access."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


class PublicationDiagnosticsTests(unittest.TestCase):
    def test_decisions_are_visible_in_logs_and_summary(self):
        workflow = (Path(__file__).resolve().parents[1] / 'workflows/check-structure.yml').read_text()
        marker = '      - name: Explain upload preparation and publication decision\n'
        self.assertIn(marker, workflow, 'Skipped upload preparation needs an executable diagnostic step')
        step = workflow.split(marker, 1)[1].split('\n  publish:', 1)[0]
        self.assertIn('if: always()', step)
        script = textwrap.dedent(step.split('        run: |\n', 1)[1])
        cases = [
            ({'EVENT_NAME': 'pull_request', 'ADMISSION_OUTCOME': 'skipped'}, 'Pull requests only run checks'),
            ({'PROCEED': 'false', 'ADMISSION_REASON': 'Target is already covered by the successful baseline.'},
             'Target is already covered by the successful baseline.'),
            ({'JOB_STATUS': 'failure', 'ADMISSION_OUTCOME': 'failure'}, 'Publication admission failed'),
            ({'JOB_STATUS': 'failure', 'PROCEED': 'true'}, 'An earlier step failed'),
            ({'JOB_STATUS': 'failure', 'EVENT_NAME': 'pull_request'}, 'An earlier step failed'),
            ({'JOB_STATUS': 'cancelled'}, 'cancelled'),
            ({'PLAN_OUTCOME': 'cancelled', 'PROCEED': 'true'}, 'cancelled'),
            ({'PLAN_OUTCOME': 'failure', 'JOB_STATUS': 'failure', 'PROCEED': 'true'}, 'Upload preparation failed'),
            ({'PLAN_OUTCOME': 'success', 'PROCEED': 'true', 'HAS_UPLOADS': 'false'}, 'manifest.json is unchanged'),
            ({'PLAN_OUTCOME': 'success', 'PROCEED': 'true', 'HAS_UPLOADS': 'true'}, 'eligible to run'),
            ({'ADMISSION_OUTCOME': 'skipped'}, 'did not grant permission'),
            ({'PROCEED': 'true'}, 'Unexpected'),
        ]
        defaults = {'EVENT_NAME': 'push', 'JOB_STATUS': 'success', 'ADMISSION_OUTCOME': 'success',
                    'PROCEED': '', 'ADMISSION_REASON': '', 'PLAN_OUTCOME': 'skipped', 'HAS_UPLOADS': ''}
        for overrides, expected in cases:
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as directory:
                summary = Path(directory) / 'summary.md'
                summary.write_text('Existing summary\n')
                result = subprocess.run(['bash', '-e', '-u', '-o', 'pipefail', '-c', script],
                                        cwd=directory, env={**os.environ, **defaults, **overrides,
                                                            'GITHUB_STEP_SUMMARY': str(summary)},
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)
                self.assertIn(expected, summary.read_text())
                self.assertTrue(summary.read_text().startswith('Existing summary\n'))
                self.assertIn('::notice::', result.stdout)
                self.assertIn('event=', result.stdout)
