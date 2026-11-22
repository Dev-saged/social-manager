import os
import tempfile
import unittest
from unittest.mock import MagicMock
from core.private_relay import R2Config, PrivateRelay, PREFIX, TTL
from core.errors import AppError
from tools.setup_private_relay import provision, LIFECYCLE


class MissingBucket(Exception):
    response = {"Error": {"Code": "NoSuchBucket"}}


class PrivateRelayTests(unittest.TestCase):
    def setUp(self):
        self.cfg = R2Config('a' * 32, 'social-manager-private-media-test', 'dummy-access', 'dummy-secret')
        self.client = MagicMock()
        self.relay = PrivateRelay(self.cfg, self.client)
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'sensitive-name.jpg')
        with open(self.path, 'wb') as f:
            f.write(b'dummy-media')

    def tearDown(self):
        self.tmp.cleanup()

    def test_upload_is_private_random_and_url_expires(self):
        self.client.generate_presigned_url.return_value = 'https://signed.example/object'
        url, handle = self.relay.upload(self.path)
        args = self.client.upload_file.call_args
        self.assertEqual(args.args[:2], (self.path, self.cfg.bucket))
        key = args.args[2]
        self.assertTrue(key.startswith(PREFIX))
        self.assertNotIn('sensitive', key)
        self.assertNotIn('ACL', args.kwargs['ExtraArgs'])
        self.assertEqual(args.kwargs['ExtraArgs']['CacheControl'], 'private, no-store')
        self.client.generate_presigned_url.assert_called_once_with('get_object', Params={'Bucket':self.cfg.bucket, 'Key':key}, ExpiresIn=TTL, HttpMethod='GET')
        self.assertEqual(handle, {'key': key})
        self.assertEqual(url, 'https://signed.example/object')
        self.assertTrue(self.relay.remove(handle))
        self.client.delete_object.assert_called_once_with(Bucket=self.cfg.bucket, Key=key)

    def test_signing_failure_attempts_cleanup_without_leaking_details(self):
        self.client.generate_presigned_url.side_effect = RuntimeError('dummy-secret signed-url-sensitive')
        with self.assertRaises(AppError) as cm:
            self.relay.upload(self.path)
        self.assertNotIn('dummy-secret', str(cm.exception))
        self.assertNotIn('signed-url-sensitive', str(cm.exception))
        self.client.delete_object.assert_called_once()

    def test_delete_failure_is_reported_without_unsafe_fallback(self):
        self.client.delete_object.side_effect = RuntimeError('dummy')
        self.assertFalse(self.relay.remove({'key': PREFIX + 'b' * 32 + '.jpg'}))

    def test_invalid_handles_cannot_delete_other_objects(self):
        for key in ('other/file.jpg', PREFIX + '../file.jpg', PREFIX + 'x.jpg'):
            with self.assertRaises(AppError):
                self.relay.remove({'key': key})
        self.client.delete_object.assert_not_called()

    def test_secret_values_are_not_in_config_representation(self):
        self.assertNotIn('dummy-access', repr(self.cfg))
        self.assertNotIn('dummy-secret', repr(self.cfg))

    def test_invalid_account_cannot_redirect_credentials(self):
        with self.assertRaises(AppError):
            PrivateRelay(R2Config('https://example.invalid', self.cfg.bucket, 'dummy', 'dummy'), self.client)

    def test_no_upload_of_unsupported_content_or_empty_file(self):
        with self.assertRaises(AppError):
            self.relay.upload(self.path, 'video')
        with open(self.path, 'wb'):
            pass
        with self.assertRaises(AppError):
            self.relay.upload(self.path)
        self.client.upload_file.assert_not_called()

    def test_provision_does_not_modify_existing_bucket(self):
        with self.assertRaises(RuntimeError):
            provision(self.client, self.cfg.bucket)
        self.client.create_bucket.assert_not_called()
        self.client.put_bucket_lifecycle_configuration.assert_not_called()

    def test_provision_requires_verified_missing_bucket_and_confirms_lifecycle(self):
        self.client.head_bucket.side_effect = MissingBucket()
        self.client.get_bucket_lifecycle_configuration.return_value = LIFECYCLE
        provision(self.client, self.cfg.bucket)
        self.client.create_bucket.assert_called_once_with(Bucket=self.cfg.bucket)
        self.client.put_bucket_lifecycle_configuration.assert_called_once_with(Bucket=self.cfg.bucket, LifecycleConfiguration=LIFECYCLE)

    def test_unknown_bucket_error_stops_before_creation(self):
        self.client.head_bucket.side_effect = RuntimeError('dummy permission error')
        with self.assertRaises(RuntimeError):
            provision(self.client, self.cfg.bucket)
        self.client.create_bucket.assert_not_called()

    def test_lifecycle_failure_is_not_reported_as_ready(self):
        self.client.head_bucket.side_effect = MissingBucket()
        self.client.get_bucket_lifecycle_configuration.return_value = {'Rules':[]}
        with self.assertRaisesRegex(RuntimeError, 'لم تُؤكد'):
            provision(self.client, self.cfg.bucket)

    def test_real_sdk_signing_is_get_only_and_expiring_without_network(self):
        from urllib.parse import urlsplit, parse_qs
        from core.private_relay import client_for
        client = client_for(self.cfg)
        url = client.generate_presigned_url('get_object', Params={'Bucket':self.cfg.bucket,'Key':PREFIX+'a'*32+'.jpg'}, ExpiresIn=TTL, HttpMethod='GET')
        parts=urlsplit(url)
        self.assertTrue(parts.hostname.endswith('.r2.cloudflarestorage.com'))
        query=parse_qs(parts.query)
        self.assertEqual(query['X-Amz-Expires'],['3600'])
        self.assertEqual(query['X-Amz-Algorithm'],['AWS4-HMAC-SHA256'])
        self.assertNotIn(self.cfg.secret_key,url)
