"""Failure evidence and conditional writes through the real SDK on loopback."""
import hashlib
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from botocore.exceptions import ClientError, ReadTimeoutError

from test_publish import GitFixture, publisher
from test_resource_sources import Store


class PublicationFailureTests(GitFixture):
    def local_store(self, baseline, reject=False):
        state = {'body': None if baseline is None else baseline.encode(), 'puts': 0}

        def etag():
            return '"' + hashlib.md5(state['body']).hexdigest() + '"'

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def error(inner, status, code):
                body = ('<Error><Code>' + code + '</Code></Error>').encode()
                inner.send_response(status)
                inner.send_header('Content-Length', str(len(body)))
                inner.end_headers()
                inner.wfile.write(body)

            def do_GET(inner):
                if state['body'] is None:
                    inner.error(404, 'NoSuchKey')
                    return
                inner.send_response(200)
                inner.send_header('Content-Length', str(len(state['body'])))
                inner.send_header('ETag', etag())
                inner.end_headers()
                inner.wfile.write(state['body'])

            def do_PUT(inner):
                state['puts'] += 1
                body = inner.rfile.read(int(inner.headers['Content-Length']))
                matches = (inner.headers.get('If-None-Match') == '*' if state['body'] is None
                           else inner.headers.get('If-Match') == etag())
                if reject or not matches:
                    inner.error(412, 'PreconditionFailed')
                    return
                state['body'] = body
                # Accept the conditional write, then lose its response on the wire.
                inner.connection.shutdown(socket.SHUT_RDWR)
                inner.connection.close()
                inner.close_connection = True

            def log_message(inner, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()

        def close_server():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(close_server)
        environment = patch.dict(os.environ, {
            'AWS_ACCESS_KEY_ID': 'fixture', 'AWS_SECRET_ACCESS_KEY': 'fixture',
            'AWS_SESSION_TOKEN': '', 'AWS_DEFAULT_REGION': 'us-east-1',
            'AWS_EC2_METADATA_DISABLED': 'true', 'AWS_DISTRIBUITION_ID': 'fixture',
            'AWS_ENDPOINT_URL_S3': 'http://127.0.0.1:' + str(server.server_port),
        })
        environment.start()
        self.addCleanup(environment.stop)
        store = publisher.S3('s3://bucket/cases')
        self.addCleanup(store.client.close)
        self.addCleanup(store.manifest_client.close)
        return store, state

    def test_lost_conditional_put_response_is_read_back_without_retry(self):
        self.entry['title'] = 'Updated title'
        self.write_manifest(); self.commit()
        plan = self.plan()
        store, remote = self.local_store(plan['baseline'])
        with patch.object(store, 'refresh_paths'), patch('botocore.endpoint.time.sleep'):
            publisher.apply(self.root, plan, store)
        self.assertEqual(remote['puts'], 1)
        self.assertEqual(remote['body'], (self.root / 'manifest.json').read_bytes())
        batch = json.loads((self.root / '.publication/batches/001/status.json').read_text())
        self.assertTrue(batch['manifest_verified'])
        self.assertEqual(batch['cdn_pending'], [])

    def test_lost_first_manifest_put_response_is_read_back_without_retry(self):
        self.entries = []
        self.write_manifest(); after = self.commit()
        plan = publisher.prepare(self.root, None, after)
        store, remote = self.local_store(None)
        with patch.object(store, 'refresh_paths'), patch('botocore.endpoint.time.sleep'):
            publisher.apply(self.root, plan, store)
        self.assertEqual(remote, {'body': b'[]', 'puts': 1})
        batch = json.loads((self.root / '.publication/batches/001/status.json').read_text())
        self.assertTrue(batch['manifest_verified'])
        self.assertEqual(batch['cdn_pending'], [])

    def test_definite_conditional_conflict_fails_even_when_manifest_matches(self):
        plan = self.plan()
        store, remote = self.local_store(plan['baseline'], reject=True)
        with patch.object(store, 'refresh_paths') as refresh, self.assertRaises(ClientError):
            publisher.apply(self.root, plan, store)
        self.assertEqual(remote['puts'], 1)
        self.assertEqual(remote['body'], (self.root / 'manifest.json').read_bytes())
        self.assertFalse(refresh.called)
        batch = json.loads((self.root / '.publication/batches/001/status.json').read_text())
        self.assertFalse(batch['manifest_committed'])

    def test_head_errors_keep_successful_uploads_in_uncommitted_evidence(self):
        self.change(); plan = self.ready(self.plan())
        key = 'a-case/A Case.png'
        errors = [ClientError({'Error': {'Code': '403', 'Message': 'denied'}}, 'HeadObject'),
                  ReadTimeoutError(endpoint_url='http://fixture.invalid')]
        for number, error in enumerate(errors):
            with self.subTest(error=type(error).__name__):
                store = Store(plan['baseline'])
                output = self.root / ('publication-' + str(number))
                with patch.object(store, 'head', side_effect=error), self.assertRaises(type(error)):
                    publisher.apply(self.root, plan, store, output)
                self.assertEqual(store.writes, [key])
                status = json.loads((output / 'status.json').read_text())
                self.assertEqual(status['steps'][0]['files'][0]['status'], 'failed')
                self.assertEqual(status['uploaded_uncommitted'], [key])

    def test_failed_repair_keeps_the_initial_upload_in_uncommitted_evidence(self):
        self.change(); plan = self.ready(self.plan())
        store = Store(plan['baseline'])
        upload = store.upload

        def fail_repair(path, key):
            if store.writes:
                raise ValueError('repair failed')
            upload(path, key)
        with patch.object(store, 'upload', side_effect=fail_repair), patch.object(store, 'head', return_value=None):
            with self.assertRaisesRegex(ValueError, 'repair failed'):
                publisher.apply(self.root, plan, store)
        status = json.loads((self.root / '.publication/status.json').read_text())
        self.assertEqual(status['uploaded_uncommitted'], ['a-case/A Case.png'])

    def test_index_only_failure_preserves_manifest_acceptance_and_verification(self):
        self.entry['title'] = 'Updated title'
        self.write_manifest(); self.commit()
        plan = self.plan()
        self.assertEqual(plan['tasks'], [])
        for phase in ('readback', 'cdn'):
            with self.subTest(phase=phase):
                store = Store(plan['baseline'])
                read_manifest = store.read_manifest

                def read():
                    if phase == 'readback' and 'manifest.json' in store.writes:
                        raise ValueError('readback failed')
                    return read_manifest()
                output = self.root / ('publication-' + phase)
                with patch.object(store, 'read_manifest', side_effect=read), patch.object(
                        store, 'refresh_cdn', side_effect=ValueError('cdn failed')):
                    with self.assertRaisesRegex(ValueError, phase + ' failed'):
                        publisher.apply(self.root, plan, store, output)
                self.assertEqual(store.writes, ['manifest.json'])
                status = json.loads((output / 'status.json').read_text())
                final = status['steps'][-1]
                self.assertEqual(final['operation'], 'FINAL_SYNC')
                self.assertEqual(final['status'], 'failed')
                self.assertTrue(final['manifest_committed'])
                self.assertEqual(final['manifest_verified'], phase == 'cdn')
                self.assertNotIn(final['id'], status['uncommitted_tasks'])
