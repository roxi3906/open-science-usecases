#!/usr/bin/env python3
"""Publish manifest resources changed by one explicit before -> after Git range."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

SHA = re.compile(r'[0-9a-f]{40}')
VERSION_TAG = 'open-science-commit'


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE).decode('utf-8')


def validate_range(root, before, after):
    # Both endpoints come from the original event (or explicit manual inputs),
    # never HEAD^, the current branch tip, or publication history.
    for label, value in [('before', before), ('after', after)]:
        if not value or not SHA.fullmatch(value) or value == '0' * 40:
            raise ValueError(label + ' must be a nonzero, complete commit SHA; no full-sync fallback')
        git(root, 'cat-file', '-e', value + '^{commit}')
    if git(root, 'rev-parse', 'HEAD').strip() != after:
        raise ValueError('The checkout must match the original after commit')
    if not ancestor(root, before, after):
        raise ValueError('before must be an ancestor of after; non-linear publication is unsupported')


def ancestor(root, older, newer):
    result = subprocess.run(['git', 'merge-base', '--is-ancestor', older, newer], cwd=root,
                            capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise ValueError('Cannot verify S3 source commit ancestry: ' + result.stderr.strip())
    return result.returncode == 0


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def candidates(root, before, after):
    root = root.resolve()
    validate_range(root, before, after)
    changed = set(git(root, 'diff', '--no-renames', '--diff-filter=AM', '--name-only', '-z',
                      before, after).split('\0')) - {''}
    manifest = json.loads(git(root, 'show', after + ':manifest.json'))
    old = {}
    if 'manifest.json' in changed:
        # Only manifest metadata is read from the old commit. Historical resource
        # files are never opened, stat'ed, or hashed to discover this push's diff.
        listing = git(root, 'ls-tree', '--name-only', before, '--', 'manifest.json')
        if listing:
            old = {case['name']: case for case in json.loads(git(root, 'show', before + ':manifest.json'))}
    files, remote = [], []
    for case in manifest:
        for kind in ('cover', 'case', 'introduction'):
            resource = case.get(kind)
            if not resource:
                continue
            if kind == 'case' and resource.get('release_url'):
                if 'manifest.json' in changed and resource != old.get(case['name'], {}).get('case'):
                    remote.append([case['name'], resource])
                continue
            if resource['path'] in changed:
                files.append((resource['path'], case['name'] + '/' + resource['file_name'], resource['bytes']))
    if 'manifest.json' in changed:
        files.append(('manifest.json', 'manifest.json', None))
    selected = []
    for relative, key, size in files:
        path = root / relative
        if path.is_symlink() or root not in path.resolve().parents:
            raise ValueError('Publication path must be a regular file inside the checkout: ' + relative)
        if size is not None and path.stat().st_size != size:
            raise ValueError('Size mismatch: ' + relative)
        # Validate only selected bytes; naming and directory rules belong to the
        # preceding structure check. Do not trust optional manifest checksums.
        expected = git(root, 'rev-parse', after + ':' + relative).strip()
        if git(root, 'hash-object', '--', str(path)).strip() != expected:
            raise ValueError('Selected file changed outside the validated checkout: ' + relative)
        selected.append({'path': relative, 'key': key, 'bytes': path.stat().st_size, 'sha256': digest(path)})
    return selected, remote


class S3:
    def __init__(self, target):
        uri = urlsplit(target)
        if uri.scheme != 's3' or not uri.netloc or uri.query or uri.fragment:
            raise ValueError('AWS_TARGET_FOLDER must be an s3://bucket[/prefix] URI')
        self.bucket = uri.netloc
        self.prefix = uri.path.strip('/')
        self.target = target.rstrip('/')

    def request(self, operation, key, *args, missing_ok=False):
        full_key = self.prefix + '/' + key if self.prefix else key
        result = subprocess.run(['aws', 's3api', operation, '--bucket', self.bucket,
                                 '--key', full_key, *args, '--output', 'json'],
                                capture_output=True, text=True)
        if result.returncode:
            if missing_ok and re.search(r'\((404|NoSuchKey|NotFound)\)', result.stderr):
                return None
            raise ValueError('S3 ' + operation + ' failed: ' + result.stderr.strip())
        return json.loads(result.stdout or '{}')

    def head(self, key):
        return self.request('head-object', key, missing_ok=True)

    def tags(self, key):
        return self.request('get-object-tagging', key)['TagSet']

    def tag(self, key, tags):
        self.request('put-object-tagging', key, '--tagging', json.dumps({'TagSet': tags}))

    def download(self, key, path):
        self.request('get-object', key, str(path))

    def upload(self, path, key, sha256, commit):
        # Metadata is stored atomically with content, including multipart uploads.
        subprocess.run(['aws', 's3', 'cp', str(path), self.target + '/' + key, '--only-show-errors',
                        '--metadata', json.dumps({'sha256': sha256, 'source-commit': commit})], check=True)


def object_status(root, item, after, s3):
    head = s3.head(item['key'])
    if head is None:
        return 'upload', []
    tags = s3.tags(item['key'])
    versions = [head.get('Metadata', {}).get('source-commit')]
    versions += [tag['Value'] for tag in tags if tag['Key'] == VERSION_TAG]
    # A retry can be queued after a newer push. Protect only destinations that
    # actually have a newer published version; still process distinct old files.
    for version in filter(None, versions):
        if not SHA.fullmatch(version):
            raise ValueError('Invalid S3 source commit for ' + item['key'])
        if version != after and ancestor(root, after, version):
            return 'superseded', tags
        if not ancestor(root, version, after):
            raise ValueError('S3 source commit is unrelated to this push: ' + item['key'])
    if head['ContentLength'] != item['bytes']:
        return 'upload', tags
    checksum = head.get('Metadata', {}).get('sha256')
    if checksum is None:
        # Existing manually synced objects may lack a trustworthy full checksum.
        # Download only this changed candidate, never historical unchanged files.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'object'
            s3.download(item['key'], path)
            checksum = digest(path)
    return ('match' if checksum == item['sha256'] else 'upload'), tags


def reconcile(root, before, after, selected, remote, s3):
    files, matches = [], []
    for item in selected:
        status, _ = object_status(root, item, after, s3)
        if status == 'upload':
            files.append(item)
        elif status == 'match':
            matches.append(item)
    return {'before': before, 'after': after, 'candidates': selected, 'files': files,
            'matches': matches, 'remote': remote}


def prepare(root, before, after, s3):
    selected, remote = candidates(root, before, after)
    return reconcile(root, before, after, selected, remote, s3)


def record_matches(root, plan, s3):
    # Matching content needs no upload, but its version must advance: otherwise
    # an old retry could overwrite a newer push that reverted to existing bytes.
    for item in plan['matches']:
        status, tags = object_status(root, item, plan['after'], s3)
        if status == 'superseded':
            continue
        if status != 'match':
            raise ValueError('S3 object changed while preparing: ' + item['key'])
        updated = [tag for tag in tags if tag['Key'] != VERSION_TAG]
        updated.append({'Key': VERSION_TAG, 'Value': plan['after']})
        if len(updated) > 10:
            raise ValueError('S3 object has no free version tag slot: ' + item['key'])
        if updated != tags:
            s3.tag(item['key'], updated)


def apply(root, plan, s3):
    selected, remote = candidates(root, plan['before'], plan['after'])
    if selected != plan['candidates'] or remote != plan['remote']:
        raise ValueError('Prepared files changed; the checkout no longer matches the plan')
    # Reconcile the same original batch on retries. Successful objects are skipped
    # and a failed-jobs-only rerun cannot overwrite a newer published destination.
    current = reconcile(root, plan['before'], plan['after'], selected, remote, s3)
    record_matches(root, current, s3)
    for name, resource in remote:
        inspect_remote(name, resource)
    for item in current['files']:
        s3.upload(root / item['path'], item['key'], item['sha256'], plan['after'])
    print('Uploaded {} local files.'.format(len(current['files'])))


def inspect_remote(name, resource):
    result = subprocess.run(
        ["curl", "--head", "--location", "--fail", "--silent", "--show-error",
         "--connect-timeout", "10", "--max-time", "30", "--max-redirs", "5",
         "--proto", "=http,https", "--proto-redir", "=http,https",
         "--url", resource["release_url"]],
        capture_output=True, check=False,
    )
    info = {"name": name, "file_name": resource["file_name"],
            "manifest_bytes": resource["bytes"]}
    if "sha256" in resource:
        info["manifest_sha256"] = resource["sha256"]
    if result.returncode:
        info["status"] = "HEAD unavailable; remote resource skipped"
        info["curl_exit_code"] = result.returncode
    else:
        headers = {}
        # HTTP field values can contain opaque non-UTF-8 bytes. Split the raw
        # header lines first so a valid 0x85 byte is not treated as a newline.
        for raw_line in result.stdout.splitlines():
            line = raw_line.decode("latin-1")
            if line.startswith("HTTP/"):
                headers = {}
            elif ":" in line:
                key, value = line.split(":", 1)
                if key.lower() in {"content-length", "content-type", "etag", "last-modified"}:
                    headers[key.lower()] = value.strip()
        info["status"] = "HEAD only; remote resource skipped"
        info["headers"] = headers
    print(json.dumps(info, ensure_ascii=False), flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before')
    parser.add_argument('--after')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--validate-range', action='store_true')
    mode.add_argument('--dry-run', action='store_true', help='List this range locally without S3 access')
    mode.add_argument('--prepare', type=Path)
    mode.add_argument('--apply-plan', type=Path)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    if args.apply_plan:
        plan = json.loads(args.apply_plan.read_text(encoding='utf-8'))
        apply(root, plan, S3(os.environ.get('AWS_TARGET_FOLDER', '')))
        return
    validate_range(root, args.before, args.after)
    if args.validate_range:
        outputs = {'before': args.before, 'after': args.after}
    elif args.dry_run:
        files, remote = candidates(root, args.before, args.after)
        for name, resource in remote:
            inspect_remote(name, resource)
        for item in files:
            print('Candidate: ' + json.dumps(item['key'], ensure_ascii=True))
        print('Validated {} local files; no S3 access.'.format(len(files)))
        return
    else:
        s3 = S3(os.environ.get('AWS_TARGET_FOLDER', ''))
        plan = prepare(root, args.before, args.after, s3)
        payload = json.dumps(plan, ensure_ascii=True, separators=(',', ':'))
        args.prepare.write_text(payload + '\n', encoding='utf-8')
        # Generate the complete upload list before recording matching versions.
        # No file content is uploaded by this step, even when all objects match.
        record_matches(root, plan, s3)
        outputs = {'has_uploads': str(bool(plan['files'])).lower(), 'plan': payload}
        for item in plan['files']:
            print('Upload/replace: ' + json.dumps(item['key'], ensure_ascii=True))
        print('Prepared {} uploads; {} existing contents match.'.format(len(plan['files']), len(plan['matches'])))
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a', encoding='utf-8') as stream:
            for key, value in outputs.items():
                stream.write(key + '=' + value + '\n')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print('Publish failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
