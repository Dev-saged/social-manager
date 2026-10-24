import datetime
import json
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

import config
from core import accounts, auto_rules, database as db, gemini, media, net, server

INTERACTION = {"steps": [
    {"type": "google_search_call", "arguments": {"queries": ["x"]}},
    {"type": "model_output", "content": [{"type": "text",
        "text": '```json\n{"title":"خبر","text":"نص طويل عن الخبر","short":"مختصر","hashtags":["#تقنية"]}\n```',
        "annotations": [{"type": "url_citation", "url": "https://example.com/a", "title": "مصدر"}]}]}]}
LEGACY = {"candidates": [{"content": {"parts": [{"text": '{"title":"t","text":"body","short":"s"}'}]},
                          "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://e.com", "title": "E"}}]}}]}


def resp(status, payload):
    r = MagicMock()
    r.status_code, r.text = status, json.dumps(payload)
    r.json.return_value = payload
    return r


class GeminiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH)
        config.DATA_DIR = self.tmp.name
        config.DB_PATH = f"{self.tmp.name}/t.db"
        config.SECRET_KEY_PATH = f"{self.tmp.name}/secret.key"
        accounts._KEY = None
        db.init_db()
        db.set_gemini_key("KEY")
        db.add_account("x", "acc_x", {}, "personal")
        db.add_account("facebook", "acc_fb", {}, "personal")
        self.ids = [a["id"] for a in db.list_accounts()]
        self.client = server.app.test_client()

    def tearDown(self):
        config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH = self.saved
        accounts._KEY = None
        self.tmp.cleanup()

    def test_key_is_stored_encrypted(self):
        self.assertNotIn("KEY", db.get_setting("gemini_api_key"))
        self.assertEqual(db.get_gemini_key(), "KEY")

    def test_extract_both_shapes(self):
        text, src = gemini._extract(INTERACTION)
        self.assertIn("```json", text)
        self.assertEqual(src[0]["url"], "https://example.com/a")
        text, src = gemini._extract(LEGACY)
        self.assertEqual(src[0]["url"], "https://e.com")

    def test_tech_news_adds_source_and_short(self):
        with patch("core.gemini.requests.post", return_value=resp(200, INTERACTION)) as post:
            out = gemini.tech_news(["قديم"])
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["tools"], [{"type": "google_search"}])
        self.assertIn("قديم", sent["input"])
        self.assertIn("https://example.com/a", out["text"])
        self.assertTrue(out["short"].endswith("https://example.com/a"))

    def test_falls_back_to_generate_content(self):
        with patch("core.gemini.requests.post", side_effect=[resp(404, {}), resp(200, LEGACY)]) as post:
            out = gemini.write_post("موضوع")
        self.assertEqual(out["text"], "body")
        self.assertIn(":generateContent", post.call_args_list[1].args[0])

    def test_errors_are_readable(self):
        with patch("core.gemini.requests.post", return_value=resp(429, {})):
            with self.assertRaises(gemini.GeminiError) as cm:
                gemini.write_post("x")
        self.assertIn("حد", str(cm.exception))

    def test_rule_creates_draft_with_short_override(self):
        long_post = {"title": "T", "text": "ن" * 400, "short": "قصير", "hashtags": [], "sources": []}
        rid = db.add_rule("r", "topic", "تقنية", self.ids, ["00:00"], "", True)
        with patch.object(gemini, "write_post", return_value=long_post):
            gid, title = auto_rules.generate_group(db.get_rule(rid), time.time() + 60)
        g = db.get_group(gid)
        self.assertEqual(g["status"], "draft")
        by = {t["platform"]: t for t in db.group_targets(gid)}
        self.assertEqual(by["x"]["text_override"], "قصير")
        self.assertEqual(by["facebook"]["text_override"], "")

    def test_run_due_respects_slots_and_runs_once(self):
        now = datetime.datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
        post = {"title": "T", "text": "نص", "short": "نص", "hashtags": [], "sources": []}
        db.add_rule("r", "news", "", self.ids, ["11:30", "23:59"], "", False)
        with patch.object(net, "online", return_value=True), patch.object(gemini, "tech_news", return_value=post) as g:
            self.assertEqual(auto_rules.run_due(now.timestamp()), 1)
            auto_rules._last_try.clear()
            self.assertEqual(auto_rules.run_due(now.timestamp()), 0)
        self.assertEqual(g.call_count, 1)
        self.assertEqual(db.list_groups()[0]["status"], "scheduled")
        self.assertEqual(db.list_rules()[0]["last_slot"][-5:], "11:30")

    def test_rules_api_validation(self):
        bad = self.client.post("/api/rules", json={"kind": "news", "times": ["25:00"], "account_ids": self.ids})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/rules", json={"kind": "news", "times": ["21:00"], "account_ids": self.ids})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.client.get("/api/rules").json[0]["times"], ["21:00"])

    def test_settings_roundtrip(self):
        self.client.post("/api/settings", json={"gemini_api_key": "NEW", "gemini_model": "m1", "missed_grace_hours": 6})
        s = self.client.get("/api/settings").json
        self.assertTrue(s["has_gemini_key"])
        self.assertEqual((s["gemini_model"], s["missed_grace_hours"]), ("m1", "6.0"))


if __name__ == "__main__":
    unittest.main()
