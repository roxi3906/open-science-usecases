#!/usr/bin/env python3
"""Plan manifest-declared changes, then upload the fixed checked-out version."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

from manifest_diff import changed_entries, git, local_resources, manifests


def declared_sha(resource):
    value = resource.get('sha256')
    # Unchanged historical declarations were optional in the old schema. Only
    # this range's affected resources must pass the new declaration checker.
    return value.lower() if isinstance(value, str) else value


def prepare(root, before, after):
    old, new, manifest_changed = manifests(root, before, after)
    previous, current = local_resources(old), local_resources(new)
    files = []
    for key, resource in current.items():
        prior = previous.get(key)
        if prior is None or declared_sha(prior) != declared_sha(resource):
            files.append({'path': resource['path'], 'key': key})
    if manifest_changed:
        files.append({'path': 'manifest.json', 'key': 'manifest.json'})
    # Share the checker's manifest authority, then narrow to changed packages.
    # Remote packages are intentionally included even though they are not uploaded.
    previous_cases = {entry['name']: entry for entry in old}
    packages = []
    for entry in changed_entries(old, new):
        prior = previous_cases.get(entry['name'], {}).get('case')
        resource = entry['case']
        if (prior is None or declared_sha(prior) != declared_sha(resource)
                or any(prior.get(key) != resource.get(key)
                       for key in ('path', 'file_name', 'release_url'))):
            packages.append({'name': entry['name'], 'resource': resource})
    return {'before': before, 'after': after, 'files': files, 'packages': packages}


def extracted_prefix(name):
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
        raise ValueError('Invalid case name for extracted prefix')
    return name + '/extracted/'


class S3:
    def __init__(self, target):
        uri = urlsplit(target)
        if uri.scheme != 's3' or not uri.netloc or uri.query or uri.fragment:
            raise ValueError('AWS_TARGET_FOLDER must be an s3://bucket[/prefix] URI')
        self.target = target.rstrip('/')

    def upload(self, path, key):
        subprocess.run(['aws', 's3', 'cp', str(path), self.target + '/' + key,
                        '--only-show-errors'], check=True)

    def clear_extracted(self, name):
        # Trailing slash prevents deleting similarly named sibling prefixes.
        subprocess.run(['aws', 's3', 'rm', self.target + '/' + extracted_prefix(name),
                        '--recursive', '--only-show-errors'], check=True)


def apply(root, plan, s3):
    # No resource revalidation: the preceding check owns that contract. Git HEAD
    # and the plan bind all uploads to the fixed target checkout.
    if git(root, 'rev-parse', 'HEAD').strip() != plan['after']:
        raise ValueError('Checkout must match the prepared target commit')
    packages = plan.get('packages', [])
    if packages:
        if plan.get('extraction_complete') is not True:
            raise ValueError('All packages must be extracted before publication')
        # Preflight all generated files before destructive S3 operations.
        keys = {item['key'] for item in plan['files']}
        for package in packages:
            if extracted_prefix(package['name']) + 'session.json' not in keys:
                raise ValueError('Extracted session is missing from the upload plan')
        for item in plan['files']:
            if not (root / item['path']).is_file():
                raise ValueError('Planned upload file is missing: ' + item['path'])
        for package in packages:
            s3.clear_extracted(package['name'])
    for item in plan['files']:
        s3.upload(root / item['path'], item['key'])
    print('Uploaded {} local files.'.format(len(plan['files'])))


def main():
    # Also guard failed-jobs-only native reruns, which can reuse old job outputs.
    if int(os.environ.get('GITHUB_RUN_ATTEMPT', '1')) != 1:
        raise ValueError('Native reruns are disabled; use workflow_dispatch on main')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before')
    parser.add_argument('--after')
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--prepare', type=Path)
    modes.add_argument('--dry-run', action='store_true')
    modes.add_argument('--apply-plan', type=Path)
    args = parser.parse_args()
    root = Path.cwd()
    if args.apply_plan:
        apply(root, json.loads(args.apply_plan.read_text()), S3(os.environ.get('AWS_TARGET_FOLDER', '')))
        return
    plan = prepare(root, args.before, args.after)
    for item in plan['files']:
        print('Upload/replace: ' + json.dumps(item['key'], ensure_ascii=True))
    print('Prepared {} local files; no S3 queries.'.format(len(plan['files'])))
    if args.prepare:
        payload = json.dumps(plan, ensure_ascii=True, separators=(',', ':'))
        args.prepare.write_text(payload + '\n')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                stream.write('has_uploads=' + str(bool(plan['files'])).lower() + '\nplan=' + payload + '\n')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print('Publish failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
