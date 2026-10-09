#!/usr/bin/env python3
"""Download changed science packages and prepare their extracted upload files."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

from manifest_diff import git
from publish import extracted_prefix


def relative_path(value):
    # Never let archive names or storage keys escape the fresh output directory.
    if (not isinstance(value, str) or not value or '\\' in value or '\x00' in value
            or any(part in ('', '.', '..') for part in value.split('/'))
            or PurePosixPath(value).is_absolute()):
        raise ValueError('Unsafe package path: ' + repr(value))
    return value


def download(url, target, resource):
    if urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('Package download requires an HTTP(S) URL')
    expected = resource.get('sha256')
    size = resource.get('bytes')
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected):
        raise ValueError('Package requires a valid SHA-256 declaration')
    if type(size) is not int or size <= 0:
        raise ValueError('Package requires a positive byte size')
    # Stream large release packages to disk, binding mutable URLs to the declaration.
    digest = hashlib.sha256()
    count = 0
    with urlopen(url, timeout=60) as response, target.open('wb') as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            count += len(chunk)
            if count > size:
                raise ValueError('Downloaded package exceeds its declared byte size')
            digest.update(chunk); output.write(chunk)
    if digest.hexdigest() != expected.lower():
        raise ValueError('Downloaded package SHA-256 does not match the manifest')
    if count != size:
        raise ValueError('Downloaded package byte size does not match the manifest')


def unpack(package, output):
    # Extract only JSON and declared objects; never extract links or execute content.
    with tarfile.open(package, 'r:gz') as archive:
        members = archive.getmembers()
        by_name = {}
        for member in members:
            relative_path(member.name.rstrip('/') if member.isdir() else member.name)
            if member.name in by_name or not (member.isfile() or member.isdir()):
                raise ValueError('Duplicate or non-regular archive member: ' + member.name)
            by_name[member.name] = member

        def read_json(name):
            member = by_name.get(name)
            if member is None or not member.isfile():
                raise ValueError('Missing package JSON: ' + name)
            return json.load(archive.extractfile(member))

        manifest = read_json('manifest.json')
        if manifest.get('format') != 'open-science-session' or manifest.get('schemaVersion') != 1:
            raise ValueError('Unsupported science package format')
        destinations = {}
        storage_keys = set()
        for item in manifest['inventory']:
            path = relative_path(item['path'])
            if path not in by_name or not by_name[path].isfile():
                raise ValueError('Missing inventory file: ' + path)
            if 'storageKey' not in item:
                continue
            key = relative_path(item['storageKey'])
            if key == 'session.json' or key.startswith('session.json/') or key in storage_keys:
                raise ValueError('Duplicate or reserved storage key: ' + key)
            storage_keys.add(key)
            destinations.setdefault(path, []).append(key)

        # Archive order keeps gzip reads sequential, including for large packages.
        for member in members:
            for key in destinations.get(member.name, []):
                target = output / key
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open('wb') as destination:
                    shutil.copyfileobj(source, destination)
        session = read_json('session.json')
        if session.get('version') != 2 or not isinstance(session.get('session'), dict):
            raise ValueError('Unsupported session JSON')

        def rewrite(value):
            # Only exact packaged references change; cwd and historical prose remain intact.
            if isinstance(value, str) and value.startswith('$DATA/') and value[6:] in storage_keys:
                return value[6:]
            if isinstance(value, dict):
                return {key: rewrite(item) for key, item in value.items()}
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            return value

        (output / 'session.json').write_text(json.dumps(rewrite(session), ensure_ascii=False) + '\n',
                                           encoding='utf-8')


def expand_plan(root, plan, output, base_url):
    if git(root, 'rev-parse', 'HEAD').strip() != plan['after']:
        raise ValueError('Checkout must match the prepared target commit')
    result = copy.deepcopy(plan)
    generated = []
    for package in plan.get('packages', []):
        prefix = extracted_prefix(package['name'])
        resource = package['resource']
        url = resource.get('release_url')
        if not url:
            url = base_url.rstrip('/') + '/' + quote(relative_path(resource['path']), safe='/')
        directory = output / package['name']
        directory.mkdir(parents=True, exist_ok=True)
        downloaded = directory / 'package.science'
        print('Downloading and extracting: ' + package['name'], flush=True)
        download(url, downloaded, resource)
        extracted = directory / 'extracted'
        if extracted.exists():
            shutil.rmtree(extracted)
        extracted.mkdir()
        unpack(downloaded, extracted)
        for path in sorted(extracted.rglob('*')):
            if path.is_file():
                generated.append({'path': str(path.resolve()),
                                  'key': prefix + path.relative_to(extracted).as_posix()})
        downloaded.unlink()
    # Publish readiness only after every download and unpack succeeds; manifest stays last.
    result['files'] = ([item for item in plan['files'] if item['key'] != 'manifest.json']
                       + generated
                       + [item for item in plan['files'] if item['key'] == 'manifest.json'])
    result['extraction_complete'] = True
    return result


def main():
    if int(os.environ.get('GITHUB_RUN_ATTEMPT', '1')) != 1:
        raise ValueError('Native reruns are disabled; use workflow_dispatch on main')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    result = expand_plan(Path.cwd(), plan, args.output.resolve(), os.environ.get('CASE_FILE_BASE_URL', ''))
    args.plan.write_text(json.dumps(result) + '\n', encoding='utf-8')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print('Extraction failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
