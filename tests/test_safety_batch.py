import concurrent.futures
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import requests
import config
from core import accounts, auto_reply, backup, database as db, errors, media, net, publisher, relay, server


class SafetyBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH, server.MEDIA_DIR
        config.DATA_DIR = self.tmp.name
        config.DB_PATH = self.tmp.name + '/test.db'
        config.SECRET_KEY_PATH = self.tmp.name + '/secret.key'
        server.MEDIA_DIR = media.media_dir()
        os.makedirs(media.media_dir())
        accounts._KEY = None
        db.init_db()
        db.add_account('telegram', 'test', {'bot_token': 'dummy', 'chat_id': 'dummy'})
        self.aid = db.list_accounts()[0]['id']
        self.targets = [{'account_id': self.aid, 'platform': 'telegram'}]
        self.client = server.app.test_client()
        self.online = patch.object(net, 'online', return_value=True)
        self.online.start()

    def tearDown(self):
        self.online.stop()
        config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH, server.MEDIA_DIR = self.saved
        accounts._KEY = None
        self.tmp.cleanup()

    def group(self):
        return db.create_group('hello', '', '', time.time() - 1, self.targets)

    def reply(self):
        db.add_account('instagram', 'test-ig', {'password': 'dummy'}, 'personal')
        aid = db.list_accounts()[-1]['id']
        rid = db.add_reply_rule(aid, 'rule', '', ['key'], False, True, ['hello'], 'private', '')
        comment = dict(comment_id='1', media_id='m1', user_id='u1', username='u', text='key')
        qid = db.queue_reply(rid, aid, comment, 'hello', 'private', time.time() - 1)
        return rid, qid

    def test_relay_is_blocked_even_when_credentials_exist(self):
        db.set_secret('github_token', 'dummy')
        db.set_setting('relay_repo', 'test/media')
        with patch.object(relay.requests, 'put') as put, patch.object(relay.requests, 'get') as get:
            with self.assertRaises(errors.AppError) as cm:
                relay.upload('/no-file-needed.jpg')
            self.assertEqual(cm.exception.code, 'relay_disabled')
            with self.assertRaises(errors.AppError):
                relay.probe()
            put.assert_not_called()
            get.assert_not_called()
        self.assertFalse(self.client.get('/api/settings').json['relay_ready'])

    def test_restore_imports_all_new_automation_paused(self):
        db.set_account_active(self.aid, False)
        rid = db.add_rule('r', 'topic', 't', [self.aid], ['10:00'], '', False, active=False)
        rr = db.add_reply_rule(self.aid, 'reply', '', ['x'], False, True, ['y'], '', '', active=False)
        db.create_group('later', '', '', time.time() + 3600, self.targets)
        blob = backup.export('test-passphrase')
        with db._conn() as c:
            for table in ('post_targets', 'post_groups', 'accounts', 'auto_rules', 'reply_rules'):
                c.execute('DELETE FROM ' + table)
        result = backup.restore('test-passphrase', blob)
        self.assertTrue(result['paused'])
        self.assertEqual(db.list_accounts()[0]['active'], 0)
        self.assertEqual(db.list_rules()[0]['active'], 0)
        self.assertEqual(db.list_rules()[0]['require_approval'], 1)
        self.assertEqual(db.list_reply_rules()[0]['active'], 0)
        self.assertEqual(db.list_groups()[0]['status'], 'draft')
        self.assertEqual(db.due_platforms(time.time() + 7200), [])

    def test_restore_does_not_reactivate_existing_paused_account(self):
        blob = backup.export('test-passphrase')
        db.set_account_active(self.aid, False)
        backup.restore('test-passphrase', blob)
        self.assertEqual(db.get_account(self.aid)['active'], 0)

    def test_plain_import_creation_can_be_paused(self):
        r = self.client.post('/api/rules', json=dict(kind='topic', topic='t', account_ids=[self.aid], times=['10:00'], active=False))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(db.get_rule(r.json['id'])['active'], 0)
        db.add_account('instagram', 'ig', {'password': 'dummy'})
        aid = db.list_accounts()[-1]['id']
        r = self.client.post('/api/reply-rules', json=dict(account_id=aid, keywords=['x'], public_replies=['hello'], active=False))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(db.list_reply_rules()[0]['active'], 0)

    def test_delete_cancels_pending_reply_and_stale_snapshot_cannot_send(self):
        rid, qid = self.reply()
        stale = db.due_replies()[0]
        db.delete_reply_rule(rid)
        adapter = MagicMock(spec=["publish_media", "reply_limiter", "reply_to_comment"])
        adapter.reply_limiter.allow.return_value = True
        self.assertFalse(auto_reply._attempt(stale, 'public', adapter))
        adapter.reply_to_comment.assert_not_called()
        row = db.list_reply_log()[0]
        self.assertEqual((row['public_status'], row['dm_status'], row['status']), ('skipped', 'skipped', 'done'))

    def test_disable_cancels_unsent_dm_but_does_not_relabel_inflight_part(self):
        rid, qid = self.reply()
        self.assertTrue(db.claim_reply_part(qid, 'public'))
        db.update_reply_rule(rid, active=False)
        row = db.list_reply_log()[0]
        self.assertEqual((row['public_status'], row['dm_status']), ('sending', 'skipped'))
        db.set_reply_part(qid, 'public', 'sent')
        self.assertEqual(db.list_reply_log()[0]['status'], 'done')
        self.assertFalse(db.claim_reply_part(qid, 'dm'))

    def test_reply_claim_is_atomic_under_concurrency(self):
        rid, qid = self.reply()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: db.claim_reply_part(qid, 'public'), range(4)))
        self.assertEqual(sum(results), 1)

    def test_retry_cannot_resurrect_deleted_rule(self):
        rid, qid = self.reply()
        db.set_reply_part(qid, 'public', 'failed')
        db.delete_reply_rule(rid)
        self.assertEqual(self.client.post(f'/api/reply-log/{qid}/retry').status_code, 409)

    def test_acknowledgement_loss_stops_without_second_send(self):
        gid = self.group()
        accepted = []
        adapter = MagicMock(spec=["publish_media", "reply_limiter", "reply_to_comment"])
        def accepted_then_timeout(*args):
            accepted.append(1)
            raise requests.ReadTimeout('dummy acknowledgement lost')
        adapter.publish_media.side_effect = accepted_then_timeout
        with patch.object(publisher, 'get_adapter', return_value=adapter):
            publisher.run_due()
            publisher.run_due(now=time.time() + 600)
        row = db.group_targets(gid)[0]
        self.assertEqual(row['status'], 'outcome_unknown')
        self.assertEqual(len(accepted), 1)
        self.assertEqual(self.client.post(f"/api/group-targets/{row['id']}/retry", json={}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/group-targets/{row['id']}/retry", json={'confirm_unknown': 'true'}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/group-targets/{row['id']}/retry", json={'confirm_unknown': True}).status_code, 200)

    def test_http_500_after_send_is_not_automatically_retried(self):
        gid = self.group()
        response = requests.Response()
        response.status_code = 503
        response._content = b'{}'
        adapter = MagicMock(spec=["publish_media", "reply_limiter", "reply_to_comment"])
        adapter.publish_media.side_effect = requests.HTTPError(response=response)
        with patch.object(publisher, 'get_adapter', return_value=adapter):
            publisher.run_due()
            publisher.run_due(now=time.time() + 600)
        self.assertEqual(db.group_targets(gid)[0]['status'], 'outcome_unknown')
        self.assertEqual(adapter.publish_media.call_count, 1)

    def test_known_rejected_rate_limit_can_be_retried(self):
        gid = self.group()
        response = requests.Response()
        response.status_code = 429
        response._content = b'{}'
        adapter = MagicMock(spec=["publish_media", "reply_limiter", "reply_to_comment"])
        adapter.publish_media.side_effect = requests.HTTPError(response=response)
        with patch.object(publisher, 'get_adapter', return_value=adapter):
            publisher.run_due()
        self.assertEqual(db.group_targets(gid)[0]['status'], 'waiting_network')

    def test_missing_remote_id_requires_review(self):
        gid = self.group()
        adapter = MagicMock(spec=["publish_media", "reply_limiter", "reply_to_comment"])
        adapter.publish_media.return_value = None
        with patch.object(publisher, 'get_adapter', return_value=adapter):
            publisher.run_due()
        self.assertEqual(db.group_targets(gid)[0]['status'], 'outcome_unknown')

    def test_duplicate_accounts_only_create_one_target(self):
        body = dict(text='hello', account_ids=[self.aid, self.aid], scheduled_at=time.time() + 600)
        result = self.client.post('/api/groups', json=body)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(db.group_targets(result.json['id'])), 1)
        gid = db.create_group('direct', '', '', time.time(), self.targets * 2)
        self.assertEqual(len(db.group_targets(gid)), 1)
        with self.assertRaises(sqlite3.IntegrityError), db._conn() as c:
            c.execute('INSERT INTO post_targets(group_id,account_id,platform,due_at) VALUES(?,?,?,?)', (gid, self.aid, 'telegram', time.time()))

    def test_http_idempotency_replays_same_result_and_rejects_changed_payload(self):
        body = dict(text='hello', account_ids=[self.aid], scheduled_at=time.time() + 600, request_key='unique-request-key-123')
        first = self.client.post('/api/groups', json=body)
        second = self.client.post('/api/groups', json=body)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json['id'], second.json['id'])
        body['text'] = 'different'
        self.assertEqual(self.client.post('/api/groups', json=body).status_code, 409)
        self.assertEqual(len(db.list_groups()), 1)

    def test_concurrent_idempotent_submissions_only_create_one_group(self):
        when = time.time() + 600
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: db.create_group('same', '', '', when, self.targets, request_key='concurrent-request'), range(4)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(db.list_groups()), 1)

    def test_restart_recovery_requires_confirmation(self):
        gid = self.group()
        target = db.claim_due_targets()[0]
        db.recover_stuck_targets(now=time.time() + 1000)
        self.assertEqual(db.get_target(target['id'])['status'], 'outcome_unknown')
        self.assertFalse(db.retry_target(target['id']))
        self.assertTrue(db.retry_target(target['id'], confirm_unknown=True))

    def test_telegram_partial_send_does_not_repeat_media(self):
        import platforms
        path = self.tmp.name + '/dummy.jpg'
        with open(path, 'wb') as f:
            f.write(b'dummy')
        adapter = platforms.get('telegram', db.get_account(self.aid))
        with patch.object(adapter, '_call', side_effect=[{'message_id': 1}, requests.ReadTimeout('dummy')]):
            with self.assertRaises(errors.DeliveryUncertain):
                adapter.publish('x' * 2000, path)

    def test_stale_rate_deferral_cannot_resurrect_deleted_reply(self):
        rid, qid = self.reply()
        stale = db.due_replies()[0]
        db.delete_reply_rule(rid)
        adapter = MagicMock()
        adapter.reply_limiter.allow.return_value = False
        auto_reply._attempt(stale, 'public', adapter)
        row = db.list_reply_log()[0]
        self.assertEqual((row['public_status'], row['dm_status'], row['status']), ('skipped', 'skipped', 'done'))

    def test_rate_rejection_after_disable_does_not_requeue_part(self):
        from platforms.base import RateLimitExceeded
        rid, qid = self.reply()
        stale = db.due_replies()[0]
        adapter = MagicMock()
        adapter.reply_limiter.allow.return_value = True
        def disable_then_reject(*args):
            db.update_reply_rule(rid, active=False)
            raise RateLimitExceeded('dummy')
        adapter.reply_to_comment.side_effect = disable_then_reject
        auto_reply._attempt(stale, 'public', adapter)
        self.assertEqual(db.list_reply_log()[0]['public_status'], 'skipped')
        self.assertEqual(db.list_reply_log()[0]['status'], 'done')

    def test_stale_poll_cannot_queue_after_rule_deletion(self):
        rid, qid = self.reply()
        aid = db.list_reply_log()[0]['account_id']
        db.delete_reply_rule(rid)
        comment = dict(comment_id='new', media_id='m1', user_id='u', username='u', text='key')
        self.assertIsNone(db.queue_reply(rid, aid, comment, 'hello', '', time.time()))

    def test_official_instagram_media_fails_preflight_without_upload(self):
        db.add_account('instagram', 'official', {'access_token':'dummy'}, 'official')
        aid = db.list_accounts()[-1]['id']
        mid = 'a' * 32 + '.jpg'
        with open(media.path_for(mid), 'wb') as f:
            f.write(b'dummy')
        result = self.client.post('/api/groups', json=dict(text='image', account_ids=[aid], media_id=mid, scheduled_at=time.time()+600))
        self.assertEqual(result.status_code, 400)
        self.assertIn('خصوصية',result.json['error'])

    def test_legacy_post_claim_is_atomic(self):
        db.schedule_post(self.aid,'legacy',None,time.time()-1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            batches = list(pool.map(lambda _: db.claim_legacy_posts(),range(4)))
        self.assertEqual(sum(len(b) for b in batches),1)
        self.assertEqual(db.claim_legacy_posts(),[])



    def test_request_replay_survives_account_removal_and_old_schedule(self):
        body = dict(text='hello',account_ids=[self.aid],scheduled_at=time.time()+600,request_key='replay-after-restart')
        first = self.client.post('/api/groups', json=body)
        self.assertEqual(first.status_code,200)
        with db._conn() as c:
            c.execute('DELETE FROM accounts WHERE id=?',(self.aid,))
        with patch('core.routes_v2.time.time',return_value=time.time()+3600):
            again = self.client.post('/api/groups',json=body)
        self.assertEqual(again.status_code,200)
        self.assertEqual(first.json['id'],again.json['id'])
        body['text']='changed'
        self.assertEqual(self.client.post('/api/groups',json=body).status_code,409)


    def test_uncertain_reply_requires_explicit_retry_confirmation(self):
        rid, qid = self.reply()
        reply = db.due_replies()[0]
        adapter = MagicMock()
        adapter.reply_limiter.allow.return_value = True
        adapter.name = 'instagram'
        adapter.reply_to_comment.side_effect = requests.Timeout('lost acknowledgement')
        auto_reply._attempt(reply, 'public', adapter)
        self.assertEqual(db.list_reply_log()[0]['public_status'], 'outcome_unknown')
        self.assertEqual(self.client.post('/api/reply-log/%s/retry' % qid).status_code,409)
        self.assertEqual(self.client.post('/api/reply-log/%s/retry' % qid,json={'confirm_unknown':'true'}).status_code,409)
        self.assertEqual(self.client.post('/api/reply-log/%s/retry' % qid,json={'confirm_unknown':True}).status_code,200)
        db.delete_reply_rule(rid)
        self.assertEqual(self.client.post('/api/reply-log/%s/retry' % qid,json={'confirm_unknown':True}).status_code,409)


    def test_official_instagram_never_silently_switches_to_personal(self):
        from platforms.instagram import InstagramPlatform
        row={'id':999,'platform':'instagram','username':'test','account_type':'official','credentials':accounts.encrypt({'access_token':'dummy','password':'dummy'})}
        adapter=InstagramPlatform(row)
        with patch.object(adapter,'_official_publish',side_effect=errors.AppError('permission')), patch.object(adapter,'_personal_publish') as personal:
            with self.assertRaises(errors.AppError):
                adapter.publish_media('hello','dummy.jpg','image')
            personal.assert_not_called()


    def test_legacy_restart_records_uncertainty_and_retains_media(self):
        mid='c'*32+'.jpg'
        db.schedule_post(self.aid,'legacy',media.path_for(mid),time.time()-1)
        self.assertEqual(len(db.claim_legacy_posts()),1)
        db.recover_legacy_posts()
        self.assertEqual(db.list_scheduled_posts()[0]['status'],'outcome_unknown')
        self.assertIn(mid,db.referenced_media_ids())
        self.assertEqual(db.claim_legacy_posts(),[])


    def test_user_can_resolve_found_post_without_resending(self):
        gid=self.group()
        tid=db.group_targets(gid)[0]['id']
        db.finish_target(tid,'outcome_unknown',error_code='outcome_unknown')
        url='/api/group-targets/%s/done'%tid
        self.assertEqual(self.client.post(url,json={}).status_code,409)
        self.assertEqual(self.client.post(url,json={'confirmed_published':'true'}).status_code,409)
        self.assertEqual(self.client.post(url,json={'confirmed_published':True}).status_code,200)
        self.assertEqual(db.get_target(tid)['status'],'published')
        self.assertEqual(db.claim_due_targets(time.time()+10,3600),[])

    def test_schema_migration_handles_old_duplicate_targets(self):
        with db._conn() as c:
            c.execute('DROP INDEX idx_active_group_account')
            c.execute('PRAGMA user_version=4')
            # Simulate a pre-upgrade pair of duplicate in-flight jobs.
            gid = c.execute("INSERT INTO post_groups(text,scheduled_at) VALUES('old',?)", (time.time(),)).lastrowid
            for state in ('publishing', 'publishing', 'pending'):
                c.execute('INSERT INTO post_targets(group_id,account_id,platform,status,due_at) VALUES(?,?,?,?,?)', (gid, self.aid, 'telegram', state, time.time()))
        db.init_db()
        statuses = [t['status'] for t in db.group_targets(gid)]
        self.assertEqual(statuses.count('outcome_unknown'), 2)
        self.assertEqual(statuses.count('cancelled'), 1)
        self.assertEqual(db.due_platforms(time.time() + 1000), [])


if __name__ == '__main__':
    unittest.main()
