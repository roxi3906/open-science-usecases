"""Thread-safe filesystem S3 SDK fake, with injected failures at the real API boundary.

calls.jsonl retains the existing fixture's operation vocabulary for CLI regression tests.
SDK argument contracts and independent per-object metadata are checked here.
"""
import hashlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import threading
from botocore.exceptions import ClientError, ReadTimeoutError


class FakeS3Client:
    def __init__(self, root, env=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.env = os.environ if env is None else env
        self.lock = threading.RLock()
        self.hooks = {}
        self.sdk_calls = []

    def call(self, operation, args):
        with self.lock:
            self.sdk_calls.append((operation, args))
        if operation in self.hooks:
            self.hooks[operation](args)

    def log(self, args):
        with (self.root / 'calls.jsonl').open('a') as stream:
            stream.write(json.dumps(args) + '\n')

    def fail(self, code, operation):
        raise ClientError({'Error': {'Code': code, 'Message': 'injected'},
                           'ResponseMetadata': {'HTTPStatusCode': int(code) if code.isdigit() else 400}}, operation)

    def metadata(self, key, value=None):
        path = self.root / 'metadata.json'
        data = json.loads(path.read_text()) if path.exists() else {}
        if value is not None:
            data[key] = value
            path.write_text(json.dumps(data))
        return data.get(key, {})

    def etag(self, path):
        return '"' + hashlib.md5(path.read_bytes()).hexdigest() + '"'

    def get_object(self, *, Bucket, Key):
        self.call('get_object', {'Bucket': Bucket, 'Key': Key})
        with self.lock:
            self.log(['s3api', 'get-object', '--bucket', Bucket, '--key', Key, '--output', 'json'])
            path = self.root / Bucket / Key
            code = self.env.get('FAKE_S3_GET_ERROR')
            if self.env.get('FAKE_S3_FAIL_READ_AFTER_PUT') and (self.root / 'manifests.jsonl').exists():
                code = 'RequestTimeout'
            if code or not path.exists():
                self.fail(code or 'NoSuchKey', 'GetObject')
            return {'Body': io.BytesIO(path.read_bytes()), 'ETag': self.etag(path)}

    def upload_file(self, Filename, Bucket, Key, *, ExtraArgs, Config):
        self.call('upload_file', {'Bucket': Bucket, 'Key': Key, 'ExtraArgs': ExtraArgs})
        assert Config.use_threads is False
        assert ExtraArgs['ContentType']
        with self.lock:
            self.log(['s3', 'cp', Filename, 's3://' + Bucket + '/' + Key,
                      '--only-show-errors', '--metadata', 'sha256=' + ExtraArgs['Metadata']['sha256']])
            if self.env.get('FAKE_S3_FAIL') == 'cp':
                self.fail('UploadFailed', 'PutObject')
            body = Path(Filename).read_bytes()
            assert hashlib.sha256(body).hexdigest() == ExtraArgs['Metadata']['sha256']
            path = self.root / Bucket / Key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            self.metadata(Bucket + '/' + Key, ExtraArgs['Metadata'])

    def head_object(self, *, Bucket, Key):
        self.call('head_object', {'Bucket': Bucket, 'Key': Key})
        with self.lock:
            self.log(['s3api', 'head-object', '--bucket', Bucket, '--key', Key, '--output', 'json'])
            path = self.root / Bucket / Key
            code = self.env.get('FAKE_S3_HEAD_ERROR')
            if code == 'timeout':
                raise ReadTimeoutError(endpoint_url='http://fixture.invalid')
            if code or not path.exists():
                self.fail(code or '404', 'HeadObject')
            return {'ContentLength': path.stat().st_size + int(bool(self.env.get('FAKE_S3_BAD_HEAD'))),
                    'Metadata': self.metadata(Bucket + '/' + Key),
                    'LastModified': datetime.now(timezone.utc), 'ETag': self.etag(path),
                    'ResponseMetadata': {'HTTPStatusCode': 200}}

    def put_object(self, *, Bucket, Key, Body, ContentType, Metadata, **condition):
        self.call('put_object', {'Bucket': Bucket, 'Key': Key, **condition})
        with self.lock:
            self.log(['s3api', 'put-object', '--bucket', Bucket, '--key', Key] +
                     (['--if-match', condition['IfMatch']] if 'IfMatch' in condition else ['--if-none-match', condition['IfNoneMatch']]))
            path = self.root / Bucket / Key
            if (self.env.get('FAKE_S3_CONFLICT') or
                ('IfMatch' in condition and (not path.exists() or self.etag(path) != condition['IfMatch'])) or
                ('IfNoneMatch' in condition and path.exists())):
                self.fail('PreconditionFailed', 'PutObject')
            history = self.root / 'manifests.jsonl'
            count = len(history.read_text().splitlines()) if history.exists() else 0
            if self.env.get('FAKE_S3_FAIL_PUT_NUMBER') == str(count + 1):
                self.fail('AccessDenied', 'PutObject')
            fail_case = self.env.get('FAKE_S3_FAIL_MANIFEST_CASE')
            if fail_case:
                old = {e['name']: e for e in json.loads(path.read_text())} if path.exists() else {}
                new = {e['name']: e for e in json.loads(Body)}
                if new.get(fail_case) != old.get(fail_case):
                    self.fail('AccessDenied', 'PutObject')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(Body)
            self.metadata(Bucket + '/' + Key, Metadata)
            with history.open('a') as stream:
                stream.write(json.dumps(json.loads(Body)) + '\n')
            if self.env.get('FAKE_S3_TIMEOUT_AFTER_PUT'):
                raise ReadTimeoutError(endpoint_url='http://fixture.invalid')
            return {'ETag': self.etag(path)}

    def get_paginator(self, name):
        assert name == 'list_objects_v2'
        client = self
        class Paginator:
            def paginate(self, *, Bucket, Prefix):
                client.call('list_objects_v2', {'Bucket': Bucket, 'Prefix': Prefix})
                with client.lock:
                    keys = sorted(p.relative_to(client.root / Bucket).as_posix()
                                  for p in (client.root / Bucket).rglob('*') if p.is_file()
                                  and p.relative_to(client.root / Bucket).as_posix().startswith(Prefix))
                for start in range(0, len(keys), 97):
                    with client.lock:
                        client.log(['s3api', 'list-objects-v2', '--prefix', Prefix])
                    yield {'Contents': [{'Key': key} for key in keys[start:start + 97]],
                           'IsTruncated': start + 97 < len(keys)}
        return Paginator()

    def delete_objects(self, *, Bucket, Delete):
        self.call('delete_objects', {'Bucket': Bucket, 'Delete': Delete})
        assert 1 <= len(Delete['Objects']) <= 1000
        with self.lock:
            self.log(['s3api', 'delete-objects', '--keys', *[o['Key'] for o in Delete['Objects']]])
            errors = []
            for obj in Delete['Objects']:
                if self.env.get('FAKE_S3_DELETE_ERROR'):
                    errors.append({'Key': obj['Key'], 'Code': 'AccessDenied'})
                else:
                    (self.root / Bucket / obj['Key']).unlink(missing_ok=True)
            return {'Errors': errors}
