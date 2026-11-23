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
        with open(path, "wb") as f:
            f.write(b"x")
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


class NewPlatformTests(unittest.TestCase):
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

    def test_new_platforms_registered(self):
        names = platforms.platform_names()
        for p in ("linkedin", "tiktok", "pinterest"):
            self.assertIn(p, names)
        caps = platforms.capabilities()
        self.assertFalse(caps["tiktok"]["text"])
        self.assertTrue(caps["tiktok"]["video"])
        self.assertTrue(caps["linkedin"]["image"])
        self.assertFalse(caps["pinterest"]["text"])
        self.assertTrue(caps["pinterest"]["image"])

    def test_linkedin_text_post(self):
        a = self.adapter("linkedin", {"access_token": "T", "person_urn": "urn:li:person:1"})
        r = ok({})
        r.headers = {"X-RestLi-Id": "urn:li:share:9"}
        with patch("platforms.linkedin.requests.post", return_value=r) as post:
            self.assertEqual(a.publish("مرحبا"), "urn:li:share:9")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["author"], "urn:li:person:1")
        self.assertEqual(body["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"], "NONE")
        self.assertEqual(post.call_args.kwargs["headers"]["X-Restli-Protocol-Version"], "2.0.0")

    def test_linkedin_person_urn_from_userinfo(self):
        a = self.adapter("linkedin", {"access_token": "T"})
        with patch("platforms.linkedin.requests.get", return_value=ok({"sub": "abc", "name": "ساجد"})) as get:
            self.assertEqual(a._person_urn(), "urn:li:person:abc")
            self.assertTrue(get.call_args.args[0].endswith("/v2/userinfo"))

    def test_linkedin_image_flow_and_uncertain_post(self):
        from core.errors import DeliveryUncertain
        a = self.adapter("linkedin", {"access_token": "T", "person_urn": "urn:li:person:1"})
        path = f"{self.tmp.name}/i.jpg"
        with open(path, "wb") as f:
            f.write(b"img")
        reg = ok({"value": {"uploadMechanism": {"com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest": {"uploadUrl": "https://up.example/x"}}, "asset": "urn:li:digitalmediaAsset:A"}})
        with patch("platforms.linkedin.requests.post", side_effect=[reg, ok(), RuntimeError("boom")]):
            with self.assertRaises(DeliveryUncertain):
                a.publish("نص", path)

    def test_tiktok_video_flow(self):
        a = self.adapter("tiktok", {"access_token": "T"})
        path = f"{self.tmp.name}/v.mp4"
        with open(path, "wb") as f:
            f.write(b"12345")
        init = ok({"data": {"publish_id": "pub1", "upload_url": "https://up.example/t"}, "error": {"code": "ok"}})
        with patch("platforms.tiktok.requests.post", return_value=init) as post, \
             patch("platforms.tiktok.requests.put", return_value=ok()) as put:
            self.assertEqual(a.publish_media("عنوان", path, "video"), "pub1")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["post_info"]["privacy_level"], "SELF_ONLY")
        self.assertEqual(body["source_info"]["video_size"], 5)
        self.assertEqual(put.call_args.kwargs["headers"]["Content-Range"], "bytes 0-4/5")

    def test_tiktok_text_refused_and_error_mapping(self):
        from core.errors import AppError
        a = self.adapter("tiktok", {"access_token": "T"})
        with self.assertRaises(NotSupported):
            a.publish("نص فقط")
        bad = ok({"error": {"code": "unaudited_client_can_only_post_to_private_accounts", "message": "m"}})
        with patch("platforms.tiktok.requests.post", return_value=bad):
            with self.assertRaises(AppError) as ctx:
                a.probe()
        self.assertEqual(ctx.exception.code, "permission")

    def test_tiktok_refresh(self):
        a = self.adapter("tiktok", {"access_token": "T", "refresh_token": "R", "client_key": "K", "client_secret": "S"})
        self.assertTrue(a.needs_refresh(10**12))
        with patch("platforms.tiktok.requests.post", return_value=ok({"access_token": "N", "refresh_token": "R2"})) as post:
            creds = a.refresh_token()
        self.assertEqual(creds["access_token"], "N")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")
        b = self.adapter("tiktok", {"access_token": "T"})
        self.assertFalse(b.needs_refresh(10**12))

    def test_pinterest_pin_base64(self):
        a = self.adapter("pinterest", {"access_token": "T", "board_id": "B1"})
        path = f"{self.tmp.name}/p.png"
        with open(path, "wb") as f:
            f.write(b"PNG")
        with patch("platforms.pinterest.requests.post", return_value=ok({"id": "pin7"})) as post:
            self.assertEqual(a.publish_media("العنوان\nالوصف", path, "image"), "pin7")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["board_id"], "B1")
        self.assertEqual(body["title"], "العنوان")
        self.assertEqual(body["media_source"]["source_type"], "image_base64")
        self.assertEqual(body["media_source"]["content_type"], "image/png")

    def test_pinterest_text_refused_and_probe(self):
        a = self.adapter("pinterest", {"access_token": "T", "board_id": "B1"})
        with self.assertRaises(NotSupported):
            a.publish("نص فقط")
        with patch("platforms.pinterest.requests.get", side_effect=[ok({"username": "u1"}), ok({"name": "لوحتي"})]):
            info = a.probe()
        self.assertEqual(info["board"], "لوحتي")
