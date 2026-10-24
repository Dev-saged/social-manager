import tempfile
import unittest
from unittest.mock import patch, MagicMock

import config
import platforms
from core import accounts, database as db
from platforms import bluesky
from platforms.base import NotSupported


def ok(payload=None):
    r = MagicMock()
    r.json.return_value = payload or {}
    r.ok = True
    return r


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH)
        config.DATA_DIR = self.tmp.name
        config.DB_PATH = f"{self.tmp.name}/t.db"
        config.SECRET_KEY_PATH = f"{self.tmp.name}/secret.key"
        accounts._KEY = None
        db.init_db()

    def tearDown(self):
        config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH = self.saved
        accounts._KEY = None
        self.tmp.cleanup()

    def adapter(self, platform, creds):
        db.add_account(platform, "u", creds, "personal")
        return platforms.get(platform, db.list_accounts()[-1])

    def test_capabilities_cover_all_platforms(self):
        caps = platforms.capabilities()
        self.assertEqual(set(caps), set(platforms.platform_names()))
        self.assertTrue(caps["telegram"]["video"])
        self.assertFalse(caps["bluesky"]["video"])
        self.assertFalse(caps["threads"]["image"])

    def test_telegram_routes_by_kind_and_splits_long_caption(self):
        a = self.adapter("telegram", {"bot_token": "T", "chat_id": "@c"})
        path = f"{self.tmp.name}/v.mp4"
        open(path, "wb").write(b"x")
        with patch("platforms.telegram.requests.post", return_value=ok({"result": {"message_id": 7}})) as post:
            self.assertEqual(a.publish_media("hi", path, "video"), "7")
            self.assertTrue(post.call_args.args[0].endswith("/sendVideo"))
            a.publish_media("x" * 2000, path, "video")
            self.assertTrue(post.call_args.args[0].endswith("/sendMessage"))
            a.publish_media("plain", None, "")
            self.assertTrue(post.call_args.args[0].endswith("/sendMessage"))

    def test_bluesky_facets_use_utf8_byte_offsets(self):
        text = "مرحبا https://a.dev #تقنية"
        facets = bluesky.build_facets(text)
        raw = text.encode("utf-8")
        link = next(f for f in facets if f["features"][0]["$type"].endswith("link"))
        tag = next(f for f in facets if f["features"][0]["$type"].endswith("tag"))
        self.assertEqual(raw[link["index"]["byteStart"]:link["index"]["byteEnd"]].decode(), "https://a.dev")
        self.assertEqual(raw[tag["index"]["byteStart"]:tag["index"]["byteEnd"]].decode(), "#تقنية")
        self.assertEqual(tag["features"][0]["tag"], "تقنية")

    def test_bluesky_publish_creates_record(self):
        a = self.adapter("bluesky", {"identifier": "me.bsky.social", "app_password": "p"})
        calls = [ok({"accessJwt": "J", "did": "did:x"}), ok({"uri": "at://post"})]
        with patch("platforms.bluesky.requests.post", side_effect=calls) as post:
            self.assertEqual(a.publish("hello"), "at://post")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["repo"], "did:x")
        self.assertEqual(body["record"]["text"], "hello")

    def test_threads_two_step_and_media_refused(self):
        a = self.adapter("threads", {"access_token": "t", "user_id": "9"})
        with patch("platforms.threads.requests.post", side_effect=[ok({"id": "c1"}), ok({"id": "p1"})]) as post:
            self.assertEqual(a.publish("hello"), "p1")
        self.assertTrue(post.call_args_list[1].args[0].endswith("/9/threads_publish"))
        with self.assertRaises(NotSupported):
            a.publish("x", "/tmp/a.jpg")

    def test_video_default_is_not_supported(self):
        a = self.adapter("threads", {"access_token": "t", "user_id": "9"})
        with self.assertRaises(NotSupported):
            a.publish_media("x", "/tmp/a.mp4", "video")


if __name__ == "__main__":
    unittest.main()
