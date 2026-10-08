"""Filesystem S3 boundary for publisher CLI integration tests; never contacts AWS."""
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

args = sys.argv[1:]
root = Path(os.environ['FAKE_S3_ROOT'])
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps(args) + '\n')


def option(name):
    return args[args.index(name) + 1]


if args[:2] == ['s3', 'cp']:
    uri = urlsplit(args[3])
    path = root / uri.netloc / uri.path.lstrip('/')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(Path(args[2]).read_bytes())
    path.with_name(path.name + '.meta').write_text(option('--metadata'))
    # A local upload replaces the current object and its tag set.
    path.with_name(path.name + '.tags').write_text('[]')
else:
    assert args[0] == 's3api', args
    path = root / option('--bucket') / option('--key')
    if not path.is_file():
        print('An error occurred (404) when calling the S3 operation', file=sys.stderr)
        sys.exit(1)
    operation = args[1]
    metadata = path.with_name(path.name + '.meta')
    tags = path.with_name(path.name + '.tags')
    if operation == 'head-object':
        print(json.dumps({'ContentLength': path.stat().st_size,
                          'Metadata': json.loads(metadata.read_text()) if metadata.exists() else {}}))
    elif operation == 'get-object':
        # The output filename follows --key KEY in the actual client command.
        Path(args[args.index('--key') + 2]).write_bytes(path.read_bytes())
        print('{}')
    elif operation == 'get-object-tagging':
        print(json.dumps({'TagSet': json.loads(tags.read_text()) if tags.exists() else []}))
    elif operation == 'put-object-tagging':
        tags.write_text(json.dumps(json.loads(option('--tagging'))['TagSet']))
        print('{}')
    else:
        raise AssertionError(args)
