"""Reusable SDK storage boundary; concurrency is owned by the publication pools."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import quote, urlsplit


def content_type(key):
    import mimetypes
    if key.lower().endswith('.science'):
        return 'application/octet-stream'
    return mimetypes.guess_type(key)[0] or 'application/octet-stream'


def uncertain_write(error):
    from botocore.exceptions import ConnectionClosedError, ReadTimeoutError, EndpointConnectionError
    return isinstance(error, (ConnectionClosedError, ReadTimeoutError, EndpointConnectionError))


class S3:
    def __init__(self, target, client=None):
        from botocore.config import Config
        from boto3.s3.transfer import TransferConfig
        uri = urlsplit(target)
        if uri.scheme != 's3' or not uri.netloc or uri.query or uri.fragment:
            raise ValueError('AWS_TARGET_FOLDER must be an s3://bucket[/prefix] URI')
        self.target = target.rstrip('/')
        self.bucket = uri.netloc
        self.prefix = uri.path.strip('/') + '/' if uri.path.strip('/') else ''
        self.manifest_key = self.prefix + 'manifest.json'
        self.manifest_client = client
        if client is None:
            import boto3
            session = boto3.session.Session()
            config = Config(
                max_pool_connections=64, retries={'mode': 'standard', 'total_max_attempts': 4},
                connect_timeout=15, read_timeout=60)
            client = session.client('s3', config=config)
            # A retried conditional PUT can turn an accepted write into a false 412.
            self.manifest_client = session.client('s3', config=config.merge(Config(
                retries={'mode': 'standard', 'total_max_attempts': 1})))
        self.client = client
        self.transfer = TransferConfig(use_threads=False, preferred_transfer_client='classic')
        self.etag = None

    def object_key(self, key):
        return self.prefix + key

    def read_manifest(self):
        from botocore.exceptions import ClientError
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self.manifest_key)
        except ClientError as error:
            if error.response['Error']['Code'] == 'NoSuchKey':
                self.etag = None
                return None
            raise
        try:
            text = response['Body'].read().decode('utf-8')
        finally:
            response['Body'].close()
        self.etag = response['ETag']
        return text

    def upload(self, path, key):
        from publish import file_integrity
        _, checksum = file_integrity(path)
        self.client.upload_file(str(path), self.bucket, self.object_key(key),
                                ExtraArgs={'Metadata': {'sha256': checksum},
                                           'ContentType': content_type(key)}, Config=self.transfer)

    def head(self, key):
        from botocore.exceptions import ClientError
        try:
            return self.client.head_object(Bucket=self.bucket, Key=self.object_key(key))
        except ClientError as error:
            if error.response['Error']['Code'] in ('404', 'NoSuchKey', 'NotFound'):
                return None
            raise

    def commit_manifest(self, path):
        body = path.read_bytes()
        condition = {'IfMatch': self.etag} if self.etag is not None else {'IfNoneMatch': '*'}
        self.manifest_client.put_object(Bucket=self.bucket, Key=self.manifest_key, Body=body,
                                       ContentType='application/json',
                                       Metadata={'sha256': hashlib.sha256(body).hexdigest()}, **condition)

    def list_keys(self, prefix):
        full_prefix = self.object_key(prefix)
        pages = self.client.get_paginator('list_objects_v2').paginate(Bucket=self.bucket, Prefix=full_prefix)
        for page in pages:
            for item in page.get('Contents', []):
                if not item['Key'].startswith(full_prefix):
                    raise ValueError('LIST returned a key outside the exact prefix')
                yield item['Key'][len(self.prefix):]

    def delete_keys(self, keys):
        keys = list(keys)
        for offset in range(0, len(keys), 1000):
            response = self.client.delete_objects(Bucket=self.bucket, Delete={
                'Objects': [{'Key': self.object_key(key)} for key in keys[offset:offset + 1000]],
                'Quiet': True})
            if response.get('Errors'):
                raise ValueError('S3 object deletion failed: ' + json.dumps(response['Errors']))

    def cdn_paths(self, names):
        return [quote('/' + self.manifest_key, safe='/')] + [
            quote('/' + self.object_key(name + '/'), safe='/') + '*' for name in sorted(set(names))]

    def refresh_cdn(self, case_name=None):
        self.refresh_paths(self.cdn_paths([case_name] if case_name else []))

    def refresh_paths(self, paths):
        distribution = os.environ['AWS_DISTRIBUITION_ID']
        result = subprocess.run(['aws', 'cloudfront', 'create-invalidation',
                                 '--distribution-id', distribution, '--output', 'json', '--paths', *paths],
                                capture_output=True, text=True, check=True)
        identifier = json.loads(result.stdout)['Invalidation']['Id']
        subprocess.run(['aws', 'cloudfront', 'wait', 'invalidation-completed',
                        '--distribution-id', distribution, '--id', identifier], check=True)
