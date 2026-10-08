"""Upload-only filesystem boundary; any S3 query makes the test fail."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

args = sys.argv[1:]
root = Path(os.environ['FAKE_S3_ROOT'])
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps(args) + '\n')
assert args[:2] == ['s3', 'cp'] and args[4:] == ['--only-show-errors'], args
uri = urlsplit(args[3])
path = root / uri.netloc / uri.path.lstrip('/')
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(Path(args[2]).read_bytes())
