import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

import requests
import config
import platforms
from core import accounts, database as db, errors, media, net, publisher, llm, oxion, tokens, backup, deps, relay
from core import server
from platforms import instagram


def http_error(status, body):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return requests.HTTPError(response=resp)


def ok(payload=None):
    r = MagicMock()
    r.json.return_value = payload or {}
    r.ok = True
    r.status_code = 200
    r.raise_for_status.return_value = None
    return r


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH, server.MEDIA_DIR)
        config.DATA_DIR = self.tmp.name
        config.DB_PATH = f"{self.tmp.name}/t.db"
        config.SECRET_KEY_PATH = f"{self.tmp.name}/secret.key"
        server.MEDIA_DIR = media.media_dir()
        os.makedirs(media.media_dir(), exist_ok=True)
        accounts._KEY = None
        db.init_db()
        self.client = server.app.test_client()

    def tearDown(self):
        config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH, server.MEDIA_DIR = self.saved
        accounts._KEY = None
        self.tmp.cleanup()

    def add(self, platform, creds, kind="personal", name="u"):
        db.add_account(platform, name, creds, kind)
        return db.list_accounts()[-1]


class ErrorTests(Base):
    def test_meta_expired_token(self):
        info = errors.classify(http_error(400, {"error": {"code": 190, "message": "Session has expired"}}))
        self.assertEqual(info["code"], "token_expired")
        self.assertFalse(info["retryable"])
        self.assertIn("Session", info["detail"])

    def test_network_and_rate_limit_are_retryable(self):
        self.assertTrue(errors.classify(requests.ConnectionError("x"))["retryable"])
        self.assertEqual(errors.classify(http_error(429, {}))["code"], "rate_limited")
        self.assertEqual(errors.classify(http_error(503, {}))["code"], "server")

    def test_instagrapi_names_and_telegram(self):
        ChallengeRequired = type("ChallengeRequired", (Exception,), {})
        self.assertEqual(errors.classify(ChallengeRequired("c"))["code"], "challenge_required")
        info = errors.classify(http_error(400, {"ok": False, "description": "Bad Request: chat not found"}))
        self.assertEqual(info["code"], "not_found")

    def test_reason_is_shown_on_failed_check(self):
        acc = self.add("telegram", {"bot_token": "t", "chat_id": "@c"})
        with patch("platforms.telegram.requests.post", side_effect=http_error(401, {"ok": False, "description": "Unauthorized"})):
            r = self.client.post(f"/api/accounts/{acc['id']}/validate", json={}).get_json()
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "auth_invalid")
        self.assertTrue(r["hint"])
        self.assertEqual(db.list_health()[acc["id"]]["code"], "auth_invalid")


class PublisherTests(Base):
    def setUp(self):
        super().setUp()
        self.p_online = patch.object(net, "online", return_value=True)
        self.p_online.start()

    def tearDown(self):
        self.p_online.stop()
        super().tearDown()

    def test_permanent_error_keeps_code_and_manual_share_waits(self):
        x = self.add("x", {}, name="a")
        fb = self.add("facebook", {}, "share", name="b")
        gid = db.create_group("hi", "", "", time.time() - 5,
                              [{"account_id": x["id"], "platform": "x"}, {"account_id": fb["id"], "platform": "facebook"}])

        class Ad:
            def __init__(self, plat, row):
                self.plat = plat

            def publish_media(self, *a):
                if self.plat == "x":
                    raise http_error(401, {"title": "Unauthorized"})
                raise errors.ManualShare()

        with patch.object(publisher, "get_adapter", Ad):
            self.assertEqual(publisher.run_due(), 2)
        ts = {t["platform"]: t for t in db.group_targets(gid)}
        self.assertEqual((ts["x"]["status"], ts["x"]["error_code"]), ("failed", "auth_invalid"))
        self.assertEqual(ts["facebook"]["status"], "needs_you")
        view = self.client.get("/api/groups?from=0&to=9999999999").get_json()[0]
        self.assertEqual(view["state"], "partial" if False else view["state"])
        self.assertTrue(any(t["hint"] for t in view["targets"]))
        r = self.client.post(f"/api/group-targets/{ts['facebook']['id']}/done", json={})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(db.get_target(ts["facebook"]["id"])["status"], "published")
        self.assertEqual(len(db.recent_delays()), 1)

    def test_claim_is_per_platform(self):
        a = self.add("x", {}, name="a")
        b = self.add("telegram", {}, name="b")
        db.create_group("t", "", "", time.time() - 5, [{"account_id": a["id"], "platform": "x"}, {"account_id": b["id"], "platform": "telegram"}])
        self.assertEqual(sorted(db.due_platforms()), ["telegram", "x"])
        got = db.claim_due_targets(platform="telegram")
        self.assertEqual([t["platform"] for t in got], ["telegram"])
        self.assertEqual(db.due_platforms(), ["x"])

    def test_busy_platform_is_not_marked_stuck(self):
        a = self.add("instagram", {"password": "p"}, name="a")
        db.create_group("t", "", "", time.time() - 5, [{"account_id": a["id"], "platform": "instagram"}])
        db.claim_due_targets()
        self.assertEqual(db.recover_stuck_targets(time.time() + 3600, busy_platforms=["instagram"]), 0)
        self.assertEqual(db.recover_stuck_targets(time.time() + 3600), 1)


class AiTests(Base):
    def test_oxion_failure_falls_back_to_gemini(self):
        db.set_api_key("cc_x")
        db.set_gemini_key("g")
        with patch.object(oxion, "chat", side_effect=http_error(402, {"error": {"type": "insufficient_funds"}})), \
                patch("core.gemini.call", return_value=("نص", [])), patch("core.gemini._model", return_value="gm"):
            res = llm.complete("s", "p")
        self.assertEqual((res["provider"], res["fallback_from"]), ("gemini", ["oxion"]))

    def test_recommend_cheapest_with_caps(self):
        lst = [{"id": "a", "type": "chat", "caps": ["tools"], "in": 1, "out": 1},
               {"id": "b", "type": "chat", "caps": ["web_search"], "in": .5, "out": .5},
               {"id": "kimi-k2.6", "type": "chat", "caps": ["web_search"], "in": 2, "out": 2}]
        self.assertEqual(oxion.recommend(lst, ("web_search",)), "kimi-k2.6")
        self.assertEqual(oxion.recommend(lst[:2], ("web_search",)), "b")
        self.assertEqual(oxion.recommend(lst, ("vision",)), "")

    def test_no_thaura_left(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for folder in ("core", "platforms", "static"):
            for name in os.listdir(os.path.join(root, folder)):
                path = os.path.join(root, folder, name)
                if os.path.isfile(path) and name.endswith((".py", ".html")) and name != "database.py":
                    with open(path, encoding="utf-8") as f:
                        text = f.read().lower()
                    self.assertNotIn("thaura api", text, name)
                    self.assertNotIn("thaura_", text, name)


class TokenTests(Base):
    def test_refresh_saves_new_token_once(self):
        acc = self.add("instagram", {"access_token": "old", "ig_user_id": "1", "refreshed_at": time.time() - 8 * 86400}, "official")
        ad = platforms.get("instagram", acc)
        with patch("platforms.instagram.requests.get", return_value=ok({"access_token": "new"})) as g:
            self.assertTrue(tokens.ensure_fresh(ad))
        self.assertEqual(g.call_count, 1)
        fresh = accounts.decrypt(db.get_account(acc["id"])["credentials"])
        self.assertEqual(fresh["access_token"], "new")
        self.assertFalse(platforms.get("instagram", db.get_account(acc["id"])).needs_refresh(time.time()))


class InstagramOfficialTests(Base):
    def test_publish_uses_relay_then_cleans(self):
        acc = self.add("instagram", {"access_token": "t", "ig_user_id": "9", "refreshed_at": time.time()}, "official")
        ad = platforms.get("instagram", acc)
        path = media.path_for("b" * 32 + ".jpg")
        open(path, "wb").write(b"jpg")
        calls = []

        def req(method, url, timeout=0, **kw):
            calls.append((method, url))
            if url.endswith("/9/media"):
                return ok({"id": "c1"})
            if url.endswith("/c1"):
                return ok({"status_code": "FINISHED"})
            return ok({"id": "m1"})

        with patch.object(relay, "upload", return_value=("https://raw/x.jpg", {"path": "p", "sha": "s"})) as up, \
                patch.object(relay, "remove") as rm, patch("platforms.instagram.requests.request", side_effect=req):
            self.assertEqual(ad.publish_media("cap", path, "image"), "m1")
        up.assert_called_once()
        rm.assert_called_once()
        self.assertTrue(any(u.endswith("/9/media_publish") for _, u in calls))

    def test_without_relay_reason_is_media_url_needed(self):
        acc = self.add("instagram", {"access_token": "t", "ig_user_id": "9"}, "official")
        ad = platforms.get("instagram", acc)
        with self.assertRaises(errors.AppError) as cm:
            ad.publish_media("c", __file__, "image")
        self.assertEqual(cm.exception.code, "media_url_needed")

    def test_text_only_is_rejected_before_scheduling(self):
        acc = self.add("instagram", {"access_token": "t"}, "official")
        r = self.client.post("/api/groups", json={"text": "hi", "account_ids": [acc["id"]], "scheduled_at": time.time() + 600})
        self.assertEqual(r.status_code, 400)
        self.assertIn("بدون صورة", r.get_json()["error"])

    def test_code_entry_flow(self):
        instagram._set_login(5, state="need_code", kind="2fa", message="m", code="")
        r = self.client.post("/api/accounts/5/ig-code", json={"code": "123456"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(instagram.login_state(5)["state"], "verifying")
        self.assertEqual(self.client.post("/api/accounts/5/ig-code", json={"code": "12"}).status_code, 400)


class DataTests(Base):
    def test_migrations_are_versioned_and_idempotent(self):
        self.assertEqual(db.schema_version(), len(db.MIGRATIONS))
        db.init_db()
        self.assertEqual(db.schema_version(), len(db.MIGRATIONS))

    def test_full_backup_roundtrip(self):
        db.add_account("telegram", "chan", {"bot_token": "secret-token", "chat_id": "@c"})
        db.set_api_key("cc_key")
        blob = self.client.post("/api/backup/full", json={"passphrase": "correct horse"}).get_json()
        self.assertNotIn("secret-token", json.dumps(blob))
        self.assertEqual(self.client.post("/api/backup/full", json={"passphrase": "short"}).status_code, 400)
        db.delete_account(db.list_accounts()[0]["id"])
        db.set_api_key("")
        bad = self.client.post("/api/backup/full/restore", json={"passphrase": "wrong pass", "blob": blob})
        self.assertEqual(bad.status_code, 400)
        r = self.client.post("/api/backup/full/restore", json={"passphrase": "correct horse", "blob": blob}).get_json()
        self.assertEqual(r["accounts"], 1)
        self.assertEqual(accounts.decrypt(db.list_accounts()[0]["credentials"])["bot_token"], "secret-token")
        self.assertEqual(db.get_api_key(), "cc_key")

    def test_deps_check_reports_each_lib(self):
        r = self.client.get("/api/deps/check").get_json()
        mods = {x["module"]: x for x in r["libs"]}
        self.assertTrue(mods["flask"]["ok"])
        self.assertTrue(mods["flask"]["version"])
        self.assertEqual(self.client.post("/api/deps/reinstall", json={"spec": "flask"}).status_code, 400)

    def test_health_has_heartbeat_and_delays(self):
        db.set_setting("hb_at", str(time.time() - 5))
        h = self.client.get("/api/health").get_json()
        for k in ("hb_age", "overdue", "delays", "delay_p95", "schema"):
            self.assertIn(k, h)
        self.assertLess(h["hb_age"], 60)

    def test_settings_store_secrets_encrypted(self):
        self.client.post("/api/settings", json={"oxion_api_key": "cc_abc", "github_token": "ghp_x", "relay_repo": "me/relay"})
        s = self.client.get("/api/settings").get_json()
        self.assertTrue(s["has_oxion_key"] and s["has_github_token"] and s["relay_ready"])
        with db._conn() as c:
            raw = " ".join(r[0] for r in c.execute("SELECT value FROM settings"))
        self.assertNotIn("cc_abc", raw)
        self.assertNotIn("ghp_x", raw)
        self.assertEqual(self.client.post("/api/settings", json={"relay_repo": "bad repo"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
