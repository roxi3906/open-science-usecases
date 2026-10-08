#!/usr/bin/env python3
"""Find the checked commit from the last actual S3 publication, not a skipped run."""

import json
import os
import re
import subprocess
import sys


def pages(endpoint, key):
    result = subprocess.check_output(["gh", "api", "--paginate", "--slurp", endpoint])
    return [item for page in json.loads(result) for item in page[key]]


def publication_base():
    # A rerun may reuse an earlier prepare job after a partial upload. Full
    # recovery also restores files reverted since that unsuccessful attempt.
    if int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")) > 1:
        return None
    repository = os.environ["GITHUB_REPOSITORY"]
    endpoint = "repos/" + repository + "/actions/"
    runs = pages(endpoint + "workflows/publish.yml/runs?per_page=100", "workflow_runs")
    latest = None
    for run in sorted(runs, key=lambda item: item["updated_at"], reverse=True):
        if (str(run["id"]) == os.environ["GITHUB_RUN_ID"] or run["event"] != "workflow_run"
                or run["head_branch"] != "main"
                or (run.get("head_repository") or {}).get("full_name") != repository):
            continue
        # Runs are ordered by creation in the API, so a rerun of an older run
        # can contain the newest upload. Stop only after its completion time.
        if latest and run["updated_at"] < latest[0]:
            break
        jobs = pages(endpoint + "runs/" + str(run["id"]) + "/jobs?filter=all&per_page=100", "jobs")
        for job in jobs:
            if job["name"] != "publish":
                continue
            for step in job.get("steps", []):
                if (step["name"] != "Publish local resources to S3"
                        or step["conclusion"] == "skipped" or not step.get("started_at")):
                    continue
                completed = step.get("completed_at") or run["updated_at"]
                candidate = (completed, job["id"], step["conclusion"], run["display_title"])
                if latest is None or candidate[:2] > latest[:2]:
                    latest = candidate
    if latest and latest[2] == "success":
        # workflow_run.head_sha belongs to the publisher's workflow revision;
        # run-name records the exact upstream commit checked out and uploaded.
        match = re.fullmatch(r"Publish ([0-9a-f]{40})", latest[3])
        if match:
            return match[1]
    # Missing/legacy history and failed uploads cannot prove S3's current state.
    # Retrying all resources is safe; using an older diff could miss a revert.
    return None


def main():
    base = publication_base()
    print("Publication base: " + (base or "full publication (no reliable successful baseline)"))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write("base=" + (base or "") + "\n")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print("Could not determine publication base: " + str(error), file=sys.stderr)
        sys.exit(1)
