#!/usr/bin/env python3
"""Admission and failure latch for the native, whole-workflow publication queue."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from manifest_diff import ancestor, commit_exists, validate_range

BRANCH = 'check-publish-state'
WAITING = {'queued', 'pending', 'waiting', 'requested'}
RECOVER = 'Use workflow_dispatch on main to recover the final net range.'


class GitHub:
    """State commits use fast-forward ref updates as compare-and-swap."""
    def __init__(self, repository, workflow_id):
        self.url = os.environ.get('GITHUB_API_URL', 'https://api.github.com') + '/repos/' + repository
        self.workflow_id = workflow_id

    def request(self, method, path, data=None, missing=False):
        body = None if data is None else json.dumps(data).encode()
        req = Request(self.url + '/' + path, data=body, method=method, headers={
            'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
            'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json',
            'X-GitHub-Api-Version': '2022-11-28',
        })
        try:
            with urlopen(req, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as error:
            if missing and error.code == 404:
                return None
            raise ValueError(f'GitHub {method} {path} failed: HTTP {error.code}; state was not assumed successful') from error

    def read(self):
        ref = self.request('GET', 'git/ref/heads/' + BRANCH, missing=True)
        if ref is None:
            return None, None
        revision = ref['object']['sha']
        content = self.request('GET', 'contents/state.json?ref=' + revision)
        if content['encoding'] != 'base64':
            raise ValueError('Unsupported publication state encoding')
        return json.loads(base64.b64decode(content['content'])), revision

    def write(self, state, revision):
        tree = self.request('POST', 'git/trees', {'tree': [{
            'path': 'state.json', 'mode': '100644', 'type': 'blob',
            'content': json.dumps(state, sort_keys=True) + '\n',
        }]})
        commit = self.request('POST', 'git/commits', {
            'message': 'chore: record publication queue state', 'tree': tree['sha'],
            'parents': [revision] if revision else [],
        })
        if revision:
            self.request('PATCH', 'git/refs/heads/' + BRANCH, {'sha': commit['sha'], 'force': False})
        else:
            self.request('POST', 'git/refs', {'ref': 'refs/heads/' + BRANCH, 'sha': commit['sha']})
        return commit['sha']

    def run(self, run_id):
        # Latest-attempt conclusions change on native reruns; only attempt 1 is authoritative.
        return self.request('GET', f'actions/runs/{run_id}/attempts/1')

    def history(self, since):
        found = []
        # GitHub limits filtered workflow listings to 1,000 results. Refuse to
        # infer a clean history if the audit horizon cannot be reached.
        for page in range(1, 11):
            response = self.request('GET', f'actions/workflows/{self.workflow_id}/runs?branch=main&per_page=100&page={page}')
            runs = response['workflow_runs']
            for run in runs:
                if run['run_number'] > since and run['event'] in ('push', 'workflow_dispatch'):
                    found.append(self.run(run['id']) if run['run_attempt'] != 1 else run)
            if len(runs) < 100 or any(run['run_number'] <= since for run in runs):
                return found
        raise ValueError('Publication history audit exceeds 1,000 runs; cannot establish queue order. ' + RECOVER)

    def ancestor(self, before, after):
        # Waiters may target commits pushed after this runner fetched its tree.
        # Compare GitHub commit metadata instead of fetching resource contents.
        comparison = self.request('GET', 'compare/' + quote(before, safe='') + '...' + quote(after, safe=''))
        if comparison['status'] not in ('ahead', 'behind', 'identical', 'diverged'):
            raise ValueError('Unknown GitHub commit relationship')
        return comparison['status'] in ('ahead', 'identical')

    def cancel(self, run_id):
        self.request('POST', f'actions/runs/{run_id}/cancel')


class Controller:
    def __init__(self, root, api, run):
        self.root, self.api, self.run = root, api, run
        self.state = self.revision = None

    def guard(self):
        if self.run['run_attempt'] != 1:
            raise ValueError('Native reruns are disabled. ' + RECOVER)
        if self.run['head_branch'] != 'main' or self.run['event'] not in ('push', 'workflow_dispatch'):
            raise ValueError('Publication admission is restricted to main push/workflow_dispatch')

    def save(self):
        self.revision = self.api.write(self.state, self.revision)

    def cancel_waiting(self):
        runs = self.api.history(self.state['audit_after'])
        waiting = [run for run in runs if run['id'] != self.run['id'] and run['status'] in WAITING]
        recoveries = [run for run in waiting if run['event'] == 'workflow_dispatch']
        for run in waiting:
            if run['event'] != 'push':
                continue
            # A queued manual recovery is a barrier. Preserve later targets; the
            # admission gate still requires an exact before==baseline boundary.
            if any(recovery['head_sha'] != run['head_sha'] and
                   self.api.ancestor(recovery['head_sha'], run['head_sha']) for recovery in recoveries):
                continue
            fresh = self.api.run(run['id'])
            if fresh['status'] in WAITING and fresh['event'] == 'push':
                self.api.cancel(run['id'])

    def block(self, reason):
        self.state['blocked'] = reason
        self.save()  # Latch failure before any best-effort cancellation API calls.
        self.cancel_waiting()
        raise ValueError(reason + ' ' + RECOVER)

    def resolve_active(self):
        active = self.state['active']
        if not active:
            return
        run = self.api.run(active['run_id'])
        if (run['head_sha'] != active['after'] or run['event'] != active['event'] or
                run['run_attempt'] != 1 or run['run_number'] != active['number']):
            self.block('Cannot verify the preceding batch identity')
        if run['status'] != 'completed':
            self.block('Preceding batch is not terminal; refusing to wait while holding the serial lock')
        if active['ready'] and run['conclusion'] == 'success':
            self.state['baseline'] = active['after']
            if active['event'] == 'workflow_dispatch':
                # A recovery acknowledges older targets by ancestry, not run
                # creation order. Retain any older, still-uncovered waiter/failure.
                uncovered = [item['run_number'] - 1 for item in self.api.history(self.state['audit_after'])
                             if item['run_number'] < active['number'] and
                             not self.api.ancestor(item['head_sha'], active['after'])]
                self.state['audit_after'] = min([active['number'], *uncovered])
                self.state['recovery'] = {'after': active['after'], 'number': active['number']}
            elif self.run['event'] != 'workflow_dispatch':
                # Run creation order is not admission order. Keep older unresolved
                # or failed runs visible until recovery covers them; their later
                # cancellation must not disappear behind a newer run number.
                unsettled = [item['run_number'] - 1 for item in self.api.history(self.state['audit_after'])
                             if item['id'] != active['run_id'] and
                             (item['status'] != 'completed' or item.get('conclusion') != 'success')]
                self.state['audit_after'] = min([active['number'], *unsettled])
            self.state['blocked'] = None
        else:
            self.state['blocked'] = f"Batch {active['run_id']} did not complete check-publish successfully"
        self.state['active'] = None

    def audit_failures(self):
        for run in self.api.history(self.state['audit_after']):
            if run['id'] == self.run['id'] or run['status'] != 'completed':
                continue
            # Old push batches covered by a confirmed recovery do not block again.
            if run['event'] == 'push' and ancestor(self.root, run['head_sha'], self.state['baseline']):
                continue
            recovery = self.state.get('recovery')
            if (run['event'] == 'workflow_dispatch' and recovery and
                    run['run_number'] < recovery['number'] and
                    self.api.ancestor(run['head_sha'], recovery['after'])):
                continue
            if run['conclusion'] != 'success':
                self.block(f"Batch {run['id']} failed/cancelled before completion (including before admission)")

    def enter(self, before=None, bootstrap=None):
        self.guard()
        self.state, self.revision = self.api.read()
        after = self.run['head_sha']
        if self.state is None:
            if self.run['event'] != 'workflow_dispatch' or not bootstrap:
                raise ValueError('Missing reliable baseline. Manually sync first, then dispatch with initial_baseline.')
            validate_range(self.root, bootstrap, after)
            self.state = {'version': 1, 'baseline': bootstrap, 'blocked': None, 'active': None, 'recovery': None,
                          'audit_after': self.run['run_number'] - 1}
        elif bootstrap:
            raise ValueError('initial_baseline is only allowed when the state branch does not exist')
        if self.state.get('version') != 1 or not self.state.get('baseline'):
            raise ValueError('Missing or unsupported publication state; no full-sync fallback')
        self.resolve_active()
        try:
            commit_exists(self.root, self.state['baseline'])
            commit_exists(self.root, after)
            if self.run['event'] == 'push':
                if self.state['blocked']:
                    self.block(self.state['blocked'])
                self.audit_failures()
                validate_range(self.root, before, after)
            baseline = self.state['baseline']
            if after != baseline and ancestor(self.root, after, baseline):
                if self.state['blocked']:
                    self.block('Recovery target is older than the successful baseline')
                self.save()
                return {'proceed': False, 'before': baseline, 'after': after}
            if self.run['event'] == 'push' and after == baseline:
                self.save()
                return {'proceed': False, 'before': before, 'after': after}
            if self.run['event'] == 'push' and before != baseline:
                self.block('Push predecessor does not match the successful baseline: missing or out-of-order batch')
            if self.run['event'] == 'workflow_dispatch':
                before = baseline
            validate_range(self.root, before, after)
        except (ValueError, subprocess.CalledProcessError) as error:
            # API errors/invalid ancestry also fail closed; never substitute a range.
            if not self.state['blocked']:
                self.state['blocked'] = str(error)
                self.save()
                self.cancel_waiting()
            raise
        self.state['active'] = {'run_id': self.run['id'], 'number': self.run['run_number'],
                                'before': before, 'after': after, 'event': self.run['event'], 'ready': False}
        self.save()
        return {'proceed': True, 'before': before, 'after': after}

    def finish(self, checked, published, has_uploads):
        self.guard()
        self.state, self.revision = self.api.read()
        if not self.state or not self.state.get('active'):
            return  # Rejected/covered admission has no owned batch to complete.
        active = self.state['active']
        if active['run_id'] != self.run['id'] or active['after'] != self.run['head_sha']:
            raise ValueError('Finalizer does not own the active batch; no state changed')
        current = self.api.run(self.run['id'])
        success = (checked == 'success' and
                   ((has_uploads == 'true' and published == 'success') or
                    (has_uploads == 'false' and published == 'skipped')) and
                   current['status'] == 'in_progress' and current.get('conclusion') is None)
        active['ready'] = success
        if not success:
            self.state['blocked'] = f"Batch {self.run['id']} failed or was interrupted"
        self.save()
        if not success:
            self.cancel_waiting()
            raise ValueError('Batch did not complete. ' + RECOVER)
        # This is provisional: only the terminal attempt-1 conclusion can promote
        # the baseline at the next admission. Cancellation after this step is safe.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('enter', 'finish'))
    args = parser.parse_args()
    if int(os.environ['GITHUB_RUN_ATTEMPT']) != 1:
        raise ValueError('Native reruns are disabled. ' + RECOVER)
    if os.environ['GITHUB_REF'] != 'refs/heads/main':
        raise ValueError('Manual recovery must target main')
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    api = GitHub(os.environ['GITHUB_REPOSITORY'], 'check-structure.yml')
    run = api.run(int(os.environ['GITHUB_RUN_ID']))
    if run['head_sha'] != os.environ['GITHUB_SHA'] or (run['event'] == 'push' and event.get('after') != run['head_sha']):
        raise ValueError('Run target differs from the fixed event SHA')
    ctl = Controller(Path.cwd(), api, run)
    if args.command == 'finish':
        needs = json.loads(os.environ['BATCH_RESULTS'])
        ctl.finish(needs['check-structure']['result'], needs['publish']['result'],
                   needs['check-structure']['outputs'].get('has_uploads', ''))
        return
    outputs = ctl.enter(before=event.get('before'), bootstrap=os.environ.get('INITIAL_BASELINE') or None)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        for key, value in outputs.items():
            stream.write(key + '=' + (str(value).lower() if isinstance(value, bool) else value) + '\n')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print('Publication queue blocked: ' + str(error), file=sys.stderr)
        sys.exit(1)
