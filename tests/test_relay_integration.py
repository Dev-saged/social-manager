import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import requests
import config
from core import accounts, database as db, relay, server
from core.errors import AppError, DeliveryUncertain
from core.private_relay import R2Config
from platforms.instagram import InstagramPlatform


class RelayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH
        config.DATA_DIR = self.tmp.name
        config.DB_PATH = self.tmp.name + '/test.db'
        config.SECRET_KEY_PATH = self.tmp.name + '/secret.key'
        accounts._KEY = None
        db.init_db()
        self.web = server.app.test_client()
        self.data = dict(r2_enabled=True, r2_account_id='a' * 32,
                         r2_bucket='social-manager-private-media-test',
                         r2_access_key_id='test-access-sensitive', r2_secret_access_key='test-secret-sensitive')

    def tearDown(self):
        config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH = self.saved
        accounts._KEY = None
        self.tmp.cleanup()

    def configure(self):
        relay.configure(self.data)

    def test_settings_store_keys_encrypted_and_never_return_them(self):
        self.assertEqual(self.web.post('/api/settings', json=self.data).status_code, 200)
        self.assertTrue(relay.configured())
        response = self.web.get('/api/settings')
        self.assertTrue(response.json['relay_ready'])
        self.assertTrue(response.json['has_r2_credentials'])
        self.assertNotIn(self.data['r2_access_key_id'], response.get_data(as_text=True))
        self.assertNotIn(self.data['r2_secret_access_key'], response.get_data(as_text=True))
        self.assertNotIn(self.data['r2_access_key_id'], db.get_setting('r2_access_key_id'))
        self.assertNotIn(self.data['r2_secret_access_key'], db.get_setting('r2_secret_access_key'))
        self.assertEqual(db.get_secret('r2_secret_access_key'), self.data['r2_secret_access_key'])

    def test_blank_key_fields_preserve_saved_credentials(self):
        self.configure()
        relay.configure(dict(r2_access_key_id='', r2_secret_access_key=''))
        self.assertTrue(relay.configured())
        self.assertEqual(db.get_secret('r2_secret_access_key'), self.data['r2_secret_access_key'])

    def test_bad_configuration_does_not_partially_change_keys_or_enable(self):
        bad = dict(self.data, r2_account_id='https://invalid.example')
        response = self.web.post('/api/settings', json=bad)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(relay.configured())
        self.assertFalse(db.get_secret('r2_secret_access_key'))
        self.assertNotIn(self.data['r2_secret_access_key'], response.get_data(as_text=True))

    def test_enabled_is_strict_boolean_and_credentials_are_required(self):
        for data in (dict(self.data, r2_enabled='false'), {'r2_enabled': True}):
            with self.assertRaises(AppError):
                relay.configure(data)
        self.assertFalse(relay.configured())

    def test_disabled_legacy_config_cannot_trigger_github_requests(self):
        db.set_setting('relay_repo', 'test/media')
        db.set_secret('github_token', 'legacy-sensitive')
        with patch.object(relay.requests, 'put') as put, patch.object(relay.requests, 'delete') as delete:
            with self.assertRaises(AppError):
                relay.upload('unused.jpg')
            self.assertFalse(relay.remove({'path': 'old.jpg', 'sha': 'old'}))
        put.assert_not_called()
        delete.assert_not_called()

    def test_upload_uses_private_adapter_and_binds_cleanup_to_original_bucket(self):
        self.configure()
        adapter = MagicMock()
        adapter.cfg = R2Config(self.data['r2_account_id'], self.data['r2_bucket'], 'a', 'b')
        adapter.upload.return_value = ('https://signed.invalid/x', {'key': 'ephemeral-media/' + 'a' * 32 + '.jpg'})
        with patch.object(relay, '_adapter', return_value=adapter):
            url, handle = relay.upload('unused.jpg')
            self.assertEqual(handle['provider'], 'r2')
            self.assertEqual(handle['bucket'], self.data['r2_bucket'])
            relay.remove(handle)
            adapter.remove.assert_called_once_with({'key': handle['key']})
            adapter.remove.reset_mock()
            self.assertFalse(relay.remove(dict(handle, bucket='another-bucket')))
            adapter.remove.assert_not_called()

    def test_disabling_keeps_keys_but_stops_new_uploads(self):
        self.configure()
        relay.configure({'r2_enabled': False})
        self.assertFalse(relay.configured())
        self.assertTrue(db.get_secret('r2_secret_access_key'))
        with self.assertRaises(AppError):
            relay.upload('unused.jpg')

    def test_preflight_requires_the_private_configuration(self):
        with patch.object(relay, 'configured', return_value=False):
            self.assertTrue(InstagramPlatform.preflight('test', 'image', 'official'))
        with patch.object(relay, 'configured', return_value=True):
            self.assertEqual(InstagramPlatform.preflight('test', 'image', 'official'), [])

    def test_probe_checks_signed_access_and_deletion_without_returning_url(self):
        self.configure()
        uploaded = {}
        def upload(path, kind='image'):
            with open(path, 'rb') as f:
                uploaded['bytes'] = f.read()
            return 'https://signed.invalid/private?signature=hidden', {'provider': 'r2'}
        def get(url, timeout):
            if '?' not in url:
                return MagicMock(status_code=400)
            if not uploaded.get('deleted'):
                return MagicMock(status_code=200, content=uploaded['bytes'])
            return MagicMock(status_code=404)
        def remove(handle):
            uploaded['deleted'] = True
            return True
        with patch.object(relay, 'upload', side_effect=upload), patch.object(relay, 'remove', side_effect=remove), \
             patch.object(relay.requests, 'get', side_effect=get):
            response = self.web.post('/api/relay/check', json={})
        self.assertTrue(response.json['ok'])
        self.assertEqual(response.json['deleted_status'], 404)
        self.assertNotIn('signature', response.get_data(as_text=True))

    def test_probe_sanitizes_unexpected_sdk_details(self):
        self.configure()
        with patch.object(relay, 'upload', side_effect=RuntimeError('sensitive-signature-and-key')):
            response = self.web.post('/api/relay/check', json={})
        self.assertFalse(response.json['ok'])
        self.assertNotIn('sensitive-signature-and-key', response.get_data(as_text=True))

    def test_reflected_signed_url_is_redacted_from_provider_error(self):
        from core.errors import classify
        response = MagicMock()
        response.status_code = 400
        response.json.return_value = {'error': {'message': 'Cannot fetch https://account.r2.cloudflarestorage.com/bucket/file.jpg?X-Amz-Signature=private-signature'}}
        info = classify(requests.HTTPError(response=response))
        self.assertNotIn('private-signature', info['detail'])
        self.assertNotIn('cloudflarestorage', info['detail'])

    def test_app_error_redacts_signed_url_before_string_conversion(self):
        error = AppError('media_unsupported', 'https://account.r2.cloudflarestorage.com/bucket/file.jpg?X-Amz-Credential=private-key')
        self.assertNotIn('private-key', str(error))
        self.assertNotIn('cloudflarestorage', error.detail)

    def _official(self):
        # Exercise the normal official path without any real provider token.
        return InstagramPlatform({'id': 3, 'platform': 'instagram', 'username': 'test',
                                  'account_type': 'official', 'credentials': accounts.encrypt({'access_token': 'dummy', 'ig_user_id': '9'})})

    def test_uncertain_meta_result_retains_remote_media_for_lifecycle(self):
        ad = self._official()
        with patch.object(relay, 'upload', return_value=('https://signed.invalid/x', {'provider': 'r2'})), \
             patch.object(relay, 'remove') as remove, patch.object(ad, '_wait_container'), \
             patch.object(ad, '_ig', side_effect=[{'id': 'container'}, requests.Timeout('dummy')]):
            with self.assertRaises(requests.Timeout):
                ad._official_publish('test', 'fixture.jpg', 'image')
            remove.assert_not_called()

    def test_missing_publish_id_is_not_success_and_retains_remote_media(self):
        ad = self._official()
        with patch.object(relay, 'upload', return_value=('https://signed.invalid/x', {'provider': 'r2'})), \
             patch.object(relay, 'remove') as remove, patch.object(ad, '_wait_container'), \
             patch.object(ad, '_ig', side_effect=[{'id': 'container'}, {}]):
            with self.assertRaises(DeliveryUncertain):
                ad._official_publish('test', 'fixture.jpg', 'image')
            remove.assert_not_called()

    def test_official_success_does_not_need_personal_client(self):
        ad = self._official()
        with patch.object(relay, 'upload', return_value=('https://signed.invalid/x', {'provider': 'r2'})), \
             patch.object(relay, 'remove') as remove, patch.object(ad, '_wait_container'), \
             patch.object(ad, '_personal_client', side_effect=AssertionError('unofficial path used')), \
             patch.object(ad, '_ig', side_effect=[{'id': 'container'}, {'id': 'post'}]):
            self.assertEqual(ad._official_publish('test', 'fixture.jpg', 'image'), 'post')
            remove.assert_called_once()
