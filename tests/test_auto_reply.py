import tempfile
import time
import unittest
from unittest.mock import patch

import config
from core import accounts, auto_reply, database as db, keywords, net, server
from platforms.base import RateLimitExceeded


class Limiter:
    def __init__(self, ok=True):
        self.ok = ok

    def allow(self):
        return self.ok


class FakeIG:
    name = "instagram"
    comments = []
    sent = []
    fail = {}
    reply_limiter = Limiter()
    dm_limiter = Limiter()

    def recent_comments(self):
        return list(FakeIG.comments)

    def reply_to_comment(self, media_id, comment_id, text):
        if "public" in FakeIG.fail:
            raise FakeIG.fail["public"]
        FakeIG.sent.append(("public", comment_id, text))

    def send_dm(self, user_id, text, comment_id=None):
        if "dm" in FakeIG.fail:
            raise FakeIG.fail["dm"]
        FakeIG.sent.append(("dm", user_id, text))


def comment(cid, text, ts, media="m1", user="u1", name="sara"):
    return {"comment_id": cid, "media_id": media, "user_id": user, "username": name, "text": text, "ts": ts}


class KeywordTests(unittest.TestCase):
    def test_arabic_aware(self):
        m = keywords.match_keywords
        self.assertEqual(m("أرسل لي المشروع 🔥", ["مشروع"]), "مشروع")
        self.assertEqual(m("اريد التفاصيل", ["التفاصيل"]), "التفاصيل")
        self.assertIsNone(m("المشروعات", ["مشروع"]))
        self.assertEqual(m("المشروعات", ["مشروع"], whole_word=False), "مشروع")
        self.assertEqual(m("كود ٠٨", ["08"]), "08")
        self.assertEqual(m("O8", ["08"]), "08")
        self.assertEqual(m("PREÇO?", ["preco"]), "preco")
        self.assertIsNone(m("", ["x"]))


class AutoReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH, config.REPLY_DELAY_RANGE)
        config.DATA_DIR = self.tmp.name
        config.DB_PATH = f"{self.tmp.name}/t.db"
        config.SECRET_KEY_PATH = f"{self.tmp.name}/secret.key"
        config.REPLY_DELAY_RANGE = (0, 0)
        accounts._KEY = None
        db.init_db()
        db.add_account("instagram", "me", {"password": "x"}, "personal")
        self.acc = db.list_accounts()[0]["id"]
        FakeIG.comments, FakeIG.sent, FakeIG.fail = [], [], {}
        FakeIG.reply_limiter, FakeIG.dm_limiter = Limiter(), Limiter()
        auto_reply._last_poll.clear()
        self.patches = [patch.object(auto_reply, "get_adapter", lambda p, r: FakeIG()),
                        patch.object(net, "online", return_value=True)]
        for p in self.patches:
            p.start()
        self.client = server.app.test_client()
        self.rid = db.add_reply_rule(self.acc, "r", "", ["مشروع"], False, True, ["وصلتك يا {name}"], "هذا الرابط", "https://x.dev")
        self.t0 = time.time()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH, config.REPLY_DELAY_RANGE = self.saved
        accounts._KEY = None
        self.tmp.cleanup()

    def test_matches_replies_once_and_dms(self):
        FakeIG.comments = [comment("1", "ابغى المشروع", self.t0 + 5), comment("2", "حلو", self.t0 + 6),
                           comment("3", "مشروع", self.t0 - 9999)]
        self.assertEqual(auto_reply.poll(self.t0 + 10), 1)
        auto_reply._last_poll.clear()
        self.assertEqual(auto_reply.poll(self.t0 + 20), 0)
        auto_reply.release(self.t0 + 30)
        self.assertEqual(FakeIG.sent[0], ("public", "1", "وصلتك يا @sara"))
        self.assertEqual(FakeIG.sent[1], ("dm", "u1", "هذا الرابط\nhttps://x.dev"))
        auto_reply.release(self.t0 + 40)
        self.assertEqual(len(FakeIG.sent), 2)
        self.assertEqual(db.list_reply_log()[0]["status"], "done")

    def test_rate_limit_defers_without_sending(self):
        FakeIG.comments = [comment("1", "مشروع", self.t0 + 5)]
        auto_reply.poll(self.t0 + 10)
        FakeIG.reply_limiter = Limiter(False)
        auto_reply.release(self.t0 + 30)
        self.assertEqual(FakeIG.sent, [])
        row = db.list_reply_log()[0]
        self.assertEqual((row["public_status"], row["status"]), ("pending", "queued"))
        self.assertGreater(row["release_at"], self.t0 + 200)

    def test_failure_is_final_until_manual_retry(self):
        FakeIG.comments = [comment("1", "مشروع", self.t0 + 5)]
        auto_reply.poll(self.t0 + 10)
        FakeIG.fail["dm"] = RuntimeError("blocked")
        auto_reply.release(self.t0 + 30)
        row = db.list_reply_log()[0]
        self.assertEqual((row["public_status"], row["dm_status"], row["status"]), ("sent", "failed", "done"))
        auto_reply.release(self.t0 + 60)
        self.assertEqual(len([s for s in FakeIG.sent if s[0] == "public"]), 1)
        FakeIG.fail.clear()
        self.assertEqual(self.client.post(f"/api/reply-log/{row['id']}/retry").status_code, 200)
        auto_reply.release(time.time() + 5)
        self.assertEqual([s[0] for s in FakeIG.sent], ["public", "dm"])

    def test_specific_post_rule_wins_and_offline_sends_nothing(self):
        db.add_reply_rule(self.acc, "s", "m9", ["مشروع"], False, True, ["خاص بالمنشور"], "", "")
        FakeIG.comments = [comment("1", "مشروع", self.t0 + 5, media="m9")]
        auto_reply.poll(self.t0 + 10)
        with patch.object(net, "online", return_value=False):
            self.assertEqual(auto_reply.release(self.t0 + 30), 0)
        auto_reply.release(self.t0 + 31)
        self.assertEqual(FakeIG.sent, [("public", "1", "خاص بالمنشور")])

    def test_stuck_sending_is_not_resent(self):
        FakeIG.comments = [comment("1", "مشروع", self.t0 + 5)]
        auto_reply.poll(self.t0 + 10)
        row = db.list_reply_log()[0]
        db.set_reply_part(row["id"], "public", "sending")
        with db._conn() as c:
            c.execute("UPDATE reply_log SET updated_at=?", (self.t0 - 3600,))
        auto_reply.release(self.t0 + 30)
        self.assertNotIn("public", [s[0] for s in FakeIG.sent])
        self.assertEqual(db.list_reply_log()[0]["public_status"], "failed")

    def test_api_validation_and_match_test(self):
        bad = self.client.post("/api/reply-rules", json={"account_id": self.acc, "keywords": []})
        self.assertEqual(bad.status_code, 400)
        http = self.client.post("/api/reply-rules", json={"account_id": self.acc, "keywords": ["a"], "dm_text": "x", "dm_link": "http://a.b"})
        self.assertEqual(http.status_code, 400)
        ok = self.client.post("/api/reply-rules", json={"account_id": self.acc, "keywords": ["a"], "dm_text": "x"})
        self.assertEqual(ok.status_code, 200)
        t = self.client.post("/api/reply-rules/test", json={"text": "ارسل المشروع", "keywords": ["مشروع"]}).json
        self.assertTrue(t["matched"])


if __name__ == "__main__":
    unittest.main()
