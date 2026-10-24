import os
import tempfile
import time
import unittest
from unittest.mock import patch

import requests
import config
from core import accounts, database as db, media, net, publisher
from core import server


class FakeAdapter:
    calls = []
    behaviour = {}

    def __init__(self, platform):
        self.platform = platform

    def publish_media(self, text, path, kind):
        FakeAdapter.calls.append((self.platform, text, path, kind))
        action = FakeAdapter.behaviour.get(self.platform)
        if isinstance(action, Exception):
            raise action
        return "id-" + self.platform


def fake_get(platform, row):
    return FakeAdapter(platform)


class GroupsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH, server.MEDIA_DIR)
        config.DATA_DIR = self.tmp.name
        config.DB_PATH = f"{self.tmp.name}/t.db"
        config.SECRET_KEY_PATH = f"{self.tmp.name}/secret.key"
        server.MEDIA_DIR = media.media_dir()
        accounts._KEY = None
        db.init_db()
        FakeAdapter.calls, FakeAdapter.behaviour = [], {}
        for plat, name in [("x", "a"), ("facebook", "b"), ("instagram", "c")]:
            db.add_account(plat, name, {}, "personal")
        self.ids = {a["platform"]: a["id"] for a in db.list_accounts()}
        self.client = server.app.test_client()
        os.makedirs(media.media_dir(), exist_ok=True)
        self.p_online = patch.object(net, "online", return_value=True)
        self.p_online.start()
        self.p_get = patch.object(publisher, "get_adapter", fake_get)
        self.p_get.start()

    def tearDown(self):
        self.p_online.stop()
        self.p_get.stop()
        config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH, server.MEDIA_DIR = self.saved
        accounts._KEY = None
        self.tmp.cleanup()

    def _video(self):
        mid = "a" * 32 + ".mp4"
        with open(media.path_for(mid), "wb") as f:
            f.write(b"v")
        return mid

    def _group(self, platforms=("x", "facebook"), media_id="", when=None, text="hello"):
        targets = [{"account_id": self.ids[p], "platform": p} for p in platforms]
        return db.create_group(text, media_id, media.kind_of(media_id), when or time.time() - 5, targets)

    def test_publishes_every_platform_once_and_cleans_media(self):
        mid = self._video()
        gid = self._group(media_id=mid)
        self.assertEqual(publisher.run_due(), 2)
        self.assertEqual(publisher.run_due(), 0)
        self.assertEqual({c[0] for c in FakeAdapter.calls}, {"x", "facebook"})
        self.assertFalse(os.path.exists(media.path_for(mid)))
        self.assertEqual(db.get_group(gid)["media_id"], "")
        self.assertEqual({t["status"] for t in db.group_targets(gid)}, {"published"})

    def test_failure_is_isolated_and_media_kept_for_retry(self):
        mid = self._video()
        FakeAdapter.behaviour["x"] = ValueError("bad credentials")
        gid = self._group(media_id=mid)
        publisher.run_due()
        st = {t["platform"]: t for t in db.group_targets(gid)}
        self.assertEqual(st["facebook"]["status"], "published")
        self.assertEqual(st["x"]["status"], "failed")
        self.assertTrue(os.path.exists(media.path_for(mid)))
        FakeAdapter.behaviour.clear()
        self.assertTrue(db.retry_target(st["x"]["id"]))
        publisher.run_due()
        self.assertFalse(os.path.exists(media.path_for(mid)))

    def test_network_error_waits_then_succeeds(self):
        FakeAdapter.behaviour["x"] = requests.exceptions.ConnectionError("down")
        gid = self._group(platforms=("x",))
        publisher.run_due()
        t = db.group_targets(gid)[0]
        self.assertEqual(t["status"], "waiting_network")
        self.assertGreater(t["next_try_at"], time.time())
        self.assertEqual(publisher.run_due(), 0)
        FakeAdapter.behaviour.clear()
        publisher.run_due(now=time.time() + 120)
        self.assertEqual(db.group_targets(gid)[0]["status"], "published")

    def test_offline_burns_no_attempts(self):
        self.p_online.stop()
        with patch.object(net, "online", return_value=False):
            gid = self._group(platforms=("x",))
            self.assertEqual(publisher.run_due(), 0)
        self.p_online.start()
        self.assertEqual(db.group_targets(gid)[0]["attempts"], 0)

    def test_missed_after_grace(self):
        gid = self._group(platforms=("x",), when=time.time() - 13 * 3600)
        publisher.run_due()
        self.assertEqual(db.group_targets(gid)[0]["status"], "missed")
        self.assertEqual(FakeAdapter.calls, [])

    def test_stuck_publishing_is_never_resent(self):
        gid = self._group(platforms=("x",))
        with db._conn() as c:
            c.execute("UPDATE post_targets SET status='publishing', updated_at=?", (time.time() - 3600,))
        publisher.run_due()
        self.assertEqual(db.group_targets(gid)[0]["status"], "failed")
        self.assertEqual(FakeAdapter.calls, [])

    def test_draft_is_not_published_until_approved(self):
        targets = [{"account_id": self.ids["x"], "platform": "x"}]
        gid = db.create_group("t", "", "", time.time() - 5, targets, status="draft")
        publisher.run_due()
        self.assertEqual(FakeAdapter.calls, [])
        r = self.client.patch(f"/api/groups/{gid}", json={"approve": True})
        self.assertEqual(r.status_code, 200)
        publisher.run_due(now=time.time() + 60)
        self.assertEqual(len(FakeAdapter.calls), 1)

    def test_api_validates_against_capabilities(self):
        when = time.time() + 600
        r = self.client.post("/api/groups", json={"text": "x" * 281, "account_ids": [self.ids["x"]], "scheduled_at": when})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/groups", json={"text": "hi", "account_ids": [self.ids["instagram"]], "scheduled_at": when})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/groups", json={
            "text": "x" * 281, "account_ids": [self.ids["x"], self.ids["facebook"]],
            "overrides": {str(self.ids["x"]): "short"}, "scheduled_at": when})
        self.assertEqual(r.status_code, 200)
        gid = r.json["id"]
        listed = self.client.get("/api/groups").json
        self.assertEqual(len(listed[0]["targets"]), 2)
        self.assertEqual(self.client.delete(f"/api/groups/{gid}").status_code, 200)
        self.assertEqual(self.client.get("/api/groups").json, [])

    def test_health(self):
        self.assertTrue(self.client.get("/api/health").json["online"])


if __name__ == "__main__":
    unittest.main()
