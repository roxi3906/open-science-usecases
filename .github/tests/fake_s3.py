"""Filesystem S3 boundary; only uploads and explicit extracted-prefix deletion."""
import json
import os
import shutil
from pathlib import Path
import sys
from urllib.parse import urlsplit

args = sys.argv[1:]
root = Path(os.environ['FAKE_S3_ROOT'])
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps(args) + '\n')
if os.environ.get('FAKE_S3_FAIL') == args[1]:
    sys.exit(1)
if args[:2] == ['s3', 'rm']:
    assert args[3:] == ['--recursive', '--only-show-errors'], args
    assert args[2].endswith('/extracted/'), args
    uri = urlsplit(args[2])
    path = root / uri.netloc / uri.path.lstrip('/')
    if path.exists():
        shutil.rmtree(path)
    sys.exit(0)
assert args[:2] == ['s3', 'cp'] and args[4:] == ['--only-show-errors'], args
uri = urlsplit(args[3])
path = root / uri.netloc / uri.path.lstrip('/')
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(Path(args[2]).read_bytes())
