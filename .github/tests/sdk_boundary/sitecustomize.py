"""Subprocess-only injection; production has no test environment switches."""
import os
if os.environ.get('FAKE_S3_ROOT'):
    import boto3.session
    from fake_sdk import FakeS3Client
    _client = FakeS3Client(os.environ['FAKE_S3_ROOT'])
    def client(self, name, **kwargs):
        assert name == 's3', 'Test attempted an unexpected AWS service'
        return _client
    boto3.session.Session.client = client
