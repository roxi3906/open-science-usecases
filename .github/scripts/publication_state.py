#!/usr/bin/env python3
"""Derive publication admission and failure blocking from GitHub Actions history."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from manifest_diff import ancestor, commit_exists, validate_range

WAITING = {'queued', 'pending', 'waiting', 'requested'}
RECOVER = 'Use workflow_dispatch on main to recover the final net range.'
WORKFLOW = '.github/workflows/check-structure.yml'
LEGACY = '.github/workflows/publish.yml'
ADMIT = 'Admit this batch under the shared serial lock'
PLAN = 'Prepare uploads from the checked manifest difference'
FINISH = 'Validate publication result and cancel waiters'
OLD_FINISH = 'Record provisional completion or latch failure and cancel waiters'


def workflow(run):
    return run['path'].split('@', 1)[0]


def order(run):
    # Run numbers belong to individual workflows; compare creation across both pipelines.
    return run['created_at'], run['id']


def passed(job, step):
    return (job.get('status') == 'completed' and job.get('conclusion') == 'success' and
            any(item['name'] == step and item['conclusion'] == 'success'
                for item in job.get('steps', [])))


def job_time(job, field):
    # GitHub's job timestamps, unlike run creation/update times, bound actual execution.
    value = job.get(field)
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else None
        if result is None or result.utcoffset() is None:
            raise ValueError('missing timezone-aware timestamp')
    except ValueError as error:
        raise ValueError('Missing or invalid legacy publication timing evidence: ' + field) from error
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    """Read retained attempt-one evidence; only waiter cancellation needs write access."""
    def __init__(self, repository):
        self.url = os.environ.get('GITHUB_API_URL', 'https://api.github.com') + '/repos/' + repository

    def request(self, method, path, text=False):
        req = Request(self.url + '/' + path, method=method, headers={
            'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        })
        try:
            with build_opener(NoRedirect).open(req, timeout=30) as response:
                raw = response.read()
        except HTTPError as error:
            # Log downloads redirect to signed storage URLs. Never forward the GitHub token.
            if text and error.code == 302:
                location = error.headers['Location']
                if urlsplit(location).scheme != 'https':
                    raise ValueError('GitHub log redirect must use HTTPS') from error
                with urlopen(location, timeout=30) as response:
                    raw = response.read()
            else:
                raise ValueError(f'GitHub {method} {path} failed: HTTP {error.code}; publication evidence unavailable') from error
        if text:
            return raw.decode('utf-8-sig')
        return json.loads(raw) if raw else None

    def run(self, run_id):
        # Native reruns never replace attempt one's publication result.
        run = self.request('GET', f'actions/runs/{run_id}/attempts/1')
        if run['id'] != run_id or run['run_attempt'] != 1:
            raise ValueError('Cannot verify attempt-one run identity')
        return run

    def pages(self, path, key):
        seen, page = set(), 1
        while True:
            rows = self.request('GET', f'{path}?per_page=100&page={page}')[key]
            fresh = [row for row in rows if row['id'] not in seen]
            if rows and not fresh:
                raise ValueError('Publication history pagination did not advance. ' + RECOVER)
            for row in fresh:
                seen.add(row['id'])
                yield row
            if len(rows) < 100:
                return
            page += 1

    def history(self):
        # Repository-wide, unfiltered pagination also finds the deleted legacy workflow
        # and avoids GitHub's 1,000-result cap for filtered run queries.
        found = []
        for run in self.pages('actions/runs', 'workflow_runs'):
            current = workflow(run) == WORKFLOW and run['event'] in ('push', 'workflow_dispatch')
            legacy = workflow(run) == LEGACY and run['event'] == 'workflow_run'
            if run['head_branch'] == 'main' and (current or legacy):
                found.append(self.run(run['id']) if run['run_attempt'] != 1 else run)
        return found

    def jobs(self, run_id):
        return list(self.pages(f'actions/runs/{run_id}/attempts/1/jobs', 'jobs'))

    def job_log(self, job_id):
        return self.request('GET', f'actions/jobs/{job_id}/logs', text=True)

    def ancestor(self, before, after):
        comparison = self.request('GET', 'compare/' + quote(before, safe='') + '...' + quote(after, safe=''))
        if comparison['status'] not in ('ahead', 'behind', 'identical', 'diverged'):
            raise ValueError('Unknown GitHub commit relationship')
        return comparison['status'] in ('ahead', 'identical')

    def cancel(self, run_id):
        self.request('POST', f'actions/runs/{run_id}/cancel')


class Controller:
    def __init__(self, root, api, run):
        self.root, self.api, self.run = root, api, run
        self.relationships = {}

    def is_ancestor(self, before, after):
        pair = (before, after)
        if pair not in self.relationships:
            # Only queued targets newer than this checkout require an API comparison.
            objects = subprocess.run(
                ['git', 'cat-file', '--batch-check=%(objecttype)'], cwd=self.root,
                input=f'{before}^{{commit}}\n{after}^{{commit}}\n',
                capture_output=True, text=True, check=True).stdout.splitlines()
            if any(line == sha + '^{commit} missing' for sha, line in zip(pair, objects)):
                result = self.api.ancestor(before, after)
            elif objects == ['commit', 'commit']:
                result = ancestor(self.root, before, after)
            else:
                raise ValueError('Cannot determine commit object availability')
            self.relationships[pair] = result
        return self.relationships[pair]

    def guard(self):
        if self.run['run_attempt'] != 1:
            raise ValueError('Native reruns are disabled. ' + RECOVER)
        if (self.run['head_branch'] != 'main' or workflow(self.run) != WORKFLOW or
                self.run['event'] not in ('push', 'workflow_dispatch')):
            raise ValueError('Publication admission is restricted to main push/workflow_dispatch')

    def publication_jobs(self, run):
        records = self.api.jobs(run['id'])
        jobs = {job['name']: job for job in records}
        if not jobs or len(jobs) != len(records):
            raise ValueError('Missing or ambiguous publication job evidence')
        return jobs

    def publication(self, run, jobs=None):
        if run['status'] != 'completed' or run['conclusion'] != 'success':
            return None
        if jobs is None:
            jobs = self.publication_jobs(run)
        publish = jobs.get('publish', {})
        if workflow(run) == LEGACY:
            if not passed(publish, 'Publish local resources to S3'):
                return None  # Superseded legacy runs are green without uploading anything.
            if not passed(publish, 'Check out the validated commit'):
                raise ValueError('Cannot verify legacy publication checkout')
            log = self.api.job_log(publish['id'])
            # workflow_run.head_sha is the default-branch SHA, not necessarily the
            # triggering SHA. Use checkout's full, actual commit output instead.
            shas = re.findall(r'\[command\][^\n]*git log -1 --format=%H\r?\n[^\S\n]*\S+ ([0-9a-f]{40})\s*(?:\n|$)', log)
            if len(shas) != 1:
                raise ValueError('Missing or ambiguous legacy checkout commit in retained logs')
            return {**run, 'head_sha': shas[0]}
        check, final = jobs.get('check-structure', {}), jobs.get('finalize', {})
        if not check:
            raise ValueError('Missing publication check job evidence')
        steps = {step['name']: step['conclusion'] for step in check.get('steps', [])}
        if ADMIT not in steps or steps.get(PLAN) == 'skipped':
            return None  # Check-only old workflows and already-covered pushes do not publish.
        if not passed(check, PLAN):
            raise ValueError('Missing successful publication plan evidence')
        # Recognize both combined-workflow generations; extraction must finish before
        # the newer publisher can replace remote package contents and publish the index.
        uploaded = (passed(publish, 'Upload resources, then manifest') or
                    (passed(publish, 'Download and extract changed science packages') and
                     passed(publish, 'Replace extracted content and upload resources, then manifest')))
        empty = publish.get('status') == 'completed' and publish.get('conclusion') == 'skipped'
        if (not passed(check, ADMIT) or
                not (passed(final, FINISH) or passed(final, OLD_FINISH)) or
                not (uploaded or empty)):
            raise ValueError('Cannot verify successful check-publish publication')
        # The finalizer verifies that a skipped publish was an explicitly empty plan.
        return run

    def legacy_publications(self, runs):
        candidates, verified = [], []
        for run in runs:
            if (workflow(run) != LEGACY or run['status'] != 'completed' or
                    run['conclusion'] != 'success'):
                continue
            jobs = self.publication_jobs(run)
            publish = jobs.get('publish', {})
            if not passed(publish, 'Publish local resources to S3'):
                continue
            if not passed(publish, 'Check out the validated commit'):
                raise ValueError('Cannot verify legacy publication checkout')
            started, completed = job_time(publish, 'started_at'), job_time(publish, 'completed_at')
            if completed < started:
                raise ValueError('Invalid legacy publication timing evidence: completion before start')
            candidates.append((completed, started, run, jobs))
        for completed, started, run, jobs in sorted(candidates, key=lambda item: item[0], reverse=True):
            # A verified full sync supersedes earlier writes only when they ended before
            # it began and their default-branch snapshot is within its actual checkout.
            # Overlapping/tied jobs and unknown or uncovered commits still need logs.
            if any(completed < later_start and self.is_ancestor(run['head_sha'], later['head_sha'])
                   for later, later_start in verified):
                continue
            publication = self.publication(run, jobs)
            publication['publication_started_at'] = started
            verified.append((publication, started))
        return [run for run, _ in verified]

    def successful_publications(self, runs):
        current, recoveries, latest = [], [], None
        # Query job evidence only when it can extend a verified baseline or recovery.
        # Scanning every old successful job would eventually exhaust GITHUB_TOKEN's budget.
        candidates = sorted((run for run in runs if workflow(run) == WORKFLOW and
                             run['status'] == 'completed' and run['conclusion'] == 'success'),
                            key=order, reverse=True)
        for run in candidates:
            covered_target = latest and self.is_ancestor(run['head_sha'], latest['head_sha'])
            covered_recovery = run['event'] != 'workflow_dispatch' or any(
                self.is_ancestor(run['head_sha'], recovery['head_sha']) for recovery in recoveries)
            if covered_target and covered_recovery:
                continue
            verified = self.publication(run)
            if verified:
                current.append(verified)
                latest = self.baseline(current)
                if verified['event'] == 'workflow_dispatch':
                    recoveries.append(verified)
        # Once a modern recovery succeeds, old publisher logs are no longer needed.
        if recoveries:
            return current
        return current + self.legacy_publications(runs)

    def baseline(self, successes):
        latest = None
        for run in successes:
            if latest is None or self.is_ancestor(latest['head_sha'], run['head_sha']):
                latest = run
            elif not self.is_ancestor(run['head_sha'], latest['head_sha']):
                raise ValueError('Successful publication targets have divergent history')
        return latest

    def audit(self, runs, successes):
        recoveries = [run for run in successes if run['event'] in ('workflow_dispatch', 'workflow_run')]
        for run in runs:
            if run['id'] == self.run['id']:
                continue
            if run['status'] in WAITING:
                continue  # The exact push predecessor check detects missing dependencies.
            if run['status'] != 'completed':
                raise ValueError('Preceding publication is not terminal; refusing to wait under the serial lock. ' + RECOVER)
            if run['conclusion'] == 'success':
                continue
            # Covered pushes are obsolete even when canceled after recovery. Manual
            # failures must also predate recovery; a new failed recovery stays blocking.
            if any(not (workflow(run) == LEGACY and workflow(recovery) == LEGACY) and
                   (run['event'] == 'push' or order(run) < order(recovery)) and
                   self.is_ancestor(run['head_sha'], recovery['head_sha'])
                   for recovery in recoveries):
                continue
            # Legacy runs can start out of creation order. A later full sync also
            # repairs failed writes, but only after every failed-run job has ended.
            legacy_recoveries = [recovery for recovery in recoveries
                                 if workflow(run) == LEGACY and workflow(recovery) == LEGACY and
                                 self.is_ancestor(run['head_sha'], recovery['head_sha'])]
            if legacy_recoveries:
                try:
                    jobs = self.publication_jobs(run).values()
                    if any(job.get('status') != 'completed' for job in jobs):
                        raise ValueError('Missing terminal legacy failure job evidence')
                    completed = max(job_time(job, 'completed_at') for job in jobs)
                except ValueError as error:
                    raise ValueError(str(error) + '. ' + RECOVER) from error
                if any(completed < recovery['publication_started_at'] for recovery in legacy_recoveries):
                    continue
            raise ValueError(f"Batch {run['id']} failed/cancelled without a covering recovery. " + RECOVER)

    def cancel_waiting(self):
        runs = self.api.history()
        waiting = [run for run in runs if workflow(run) == WORKFLOW and
                   run['id'] != self.run['id'] and run['status'] in WAITING]
        recoveries = [run for run in waiting if run['event'] == 'workflow_dispatch']
        for run in waiting:
            if run['event'] != 'push':
                continue
            if any(recovery['head_sha'] != run['head_sha'] and
                   self.is_ancestor(recovery['head_sha'], run['head_sha']) for recovery in recoveries):
                continue
            fresh = self.api.run(run['id'])
            if fresh['status'] in WAITING and fresh['event'] == 'push':
                self.api.cancel(run['id'])

    def enter(self, before=None, bootstrap=None):
        self.guard()
        runs = self.api.history()
        successes = self.successful_publications(runs)
        baseline = self.baseline(successes)
        after = self.run['head_sha']
        if baseline:
            if bootstrap:
                raise ValueError('initial_baseline cannot override successful publication history')
            base = baseline['head_sha']
        else:
            if self.run['event'] != 'workflow_dispatch' or not bootstrap:
                raise ValueError('Missing reliable publication baseline in Actions history. Manually sync first, then dispatch with initial_baseline.')
            base = bootstrap
        commit_exists(self.root, base)
        commit_exists(self.root, after)
        if self.run['event'] == 'push':
            self.audit(runs, successes)
            validate_range(self.root, before, after)
            if self.is_ancestor(after, base):
                return {'proceed': False, 'before': before, 'after': after,
                        'reason': f'Target {after} is already covered by the successful baseline {base}.'}
            if before != base:
                raise ValueError('Push predecessor does not match the successful baseline: missing or out-of-order batch. ' + RECOVER)
        else:
            if after != base and self.is_ancestor(after, base):
                raise ValueError('Recovery target is older than the successful baseline')
            # A manual recovery bypasses failed terminal runs, never an active publisher.
            if any(run['id'] != self.run['id'] and run['status'] not in WAITING | {'completed'} for run in runs):
                raise ValueError('Preceding publication is not terminal. ' + RECOVER)
            before = base
        validate_range(self.root, before, after)
        print(f"Publication baseline: {base}" + (f" (run {baseline['id']})" if baseline else ' (explicit initial sync)'))
        return {'proceed': True, 'before': before, 'after': after}

    def finish(self, checked, published, has_uploads, proceed='true'):
        self.guard()
        current = self.api.run(self.run['id'])
        if current['status'] != 'in_progress' or current['head_sha'] != self.run['head_sha']:
            raise ValueError('Finalizer no longer owns an active run; no waiters cancelled')
        success = (checked == 'success' and
                   ((has_uploads == 'true' and published == 'success') or
                    (has_uploads == 'false' and published == 'skipped') or
                    (proceed == 'false' and published == 'skipped')))
        if not success:
            # The failed attempt itself is the persistent latch, even if cancellation
            # APIs fail or this finalizer is interrupted. Later admission audits it.
            self.cancel_waiting()
            raise ValueError('Batch did not complete. ' + RECOVER)
        # No state is written. Only the entire attempt's final success is authoritative.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('enter', 'finish'))
    args = parser.parse_args()
    if int(os.environ['GITHUB_RUN_ATTEMPT']) != 1:
        raise ValueError('Native reruns are disabled. ' + RECOVER)
    if os.environ['GITHUB_REF'] != 'refs/heads/main':
        raise ValueError('Manual recovery must target main')
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    api = GitHub(os.environ['GITHUB_REPOSITORY'])
    run = api.run(int(os.environ['GITHUB_RUN_ID']))
    if run['head_sha'] != os.environ['GITHUB_SHA'] or (run['event'] == 'push' and event.get('after') != run['head_sha']):
        raise ValueError('Run target differs from the fixed event SHA')
    ctl = Controller(Path.cwd(), api, run)
    if args.command == 'finish':
        needs = json.loads(os.environ['BATCH_RESULTS'])
        outputs = needs['check-structure']['outputs']
        ctl.finish(needs['check-structure']['result'], needs['publish']['result'],
                   outputs.get('has_uploads', ''), outputs.get('proceed', ''))
        return
    outputs = ctl.enter(before=event.get('before'), bootstrap=os.environ.get('INITIAL_BASELINE') or None)
    if not outputs['proceed']:
        print('Publication skipped: ' + outputs['reason'])
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        for key, value in outputs.items():
            stream.write(key + '=' + (str(value).lower() if isinstance(value, bool) else value) + '\n')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print('Publication queue blocked: ' + str(error), file=sys.stderr)
        sys.exit(1)
