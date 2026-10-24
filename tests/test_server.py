import io
import tempfile
import unittest
from unittest.mock import patch

import config
from core import accounts, database as db
from core import server


class ServerBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = config.DATA_DIR
        self.original_db_path = config.DB_PATH
        self.original_key_path = config.SECRET_KEY_PATH
        self.original_media_dir = server.MEDIA_DIR
        config.DATA_DIR = self.temp_dir.name
        config.DB_PATH = f"{self.temp_dir.name}/test.db"
        config.SECRET_KEY_PATH = f"{self.temp_dir.name}/secret.key"
        server.MEDIA_DIR = f"{self.temp_dir.name}/media"
        accounts._KEY = None
        db.init_db()
        self.client = server.app.test_client()

    def tearDown(self):
        config.DATA_DIR = self.original_data_dir
        config.DB_PATH = self.original_db_path
        config.SECRET_KEY_PATH = self.original_key_path
        server.MEDIA_DIR = self.original_media_dir
        accounts._KEY = None
        self.temp_dir.cleanup()

    def test_rejects_foreign_hosts_and_origins(self):
        self.assertEqual(self.client.get("/api/accounts").status_code, 200)
        self.assertEqual(self.client.get("/api/accounts", headers={"Host": "evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/accounts", headers={"Origin": "https://evil.example"}).status_code, 403)

    def test_account_credentials_are_validated(self):
        missing = self.client.post("/api/accounts", json={"platform": "x", "username": "sample", "credentials": {}})
        self.assertEqual(missing.status_code, 400)
        valid = self.client.post("/api/accounts", json={
            "platform": "x", "username": "sample",
            "credentials": {"api_key": "key", "api_secret": "secret", "access_token": "token", "access_token_secret": "secret"},
        })
        self.assertEqual(valid.status_code, 200)

        invalid_type = self.client.post("/api/accounts", json={
            "platform": "instagram", "username": "sample", "type": "creator",
            "credentials": {"password": "secret"},
        })
        self.assertEqual(invalid_type.status_code, 400)

    def test_media_upload_and_pending_post_cancellation(self):
        invalid = self.client.post("/api/media", data={"file": (io.BytesIO(b"x"), "payload.exe")})
        self.assertEqual(invalid.status_code, 400)
        uploaded = self.client.post("/api/media", data={"file": (io.BytesIO(b"jpg"), "photo.jpg")})
        self.assertEqual(uploaded.status_code, 200)
        from pathlib import Path
        self.assertTrue((Path(server.MEDIA_DIR) / uploaded.json["media_id"]).is_file())

        db.schedule_post(1, "caption", None, 9999999999)
        post_id = db.list_scheduled_posts()[0]["id"]
        self.assertEqual(self.client.delete(f"/api/posts/{post_id}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/posts/{post_id}").status_code, 404)

    def test_uploaded_youtube_video_can_be_scheduled(self):
        db.add_account("youtube", "my-channel", {})
        account_id = db.list_accounts()[0]["id"]
        uploaded = self.client.post("/api/media", data={
            "file": (io.BytesIO(b"video"), "clip.mp4"),
        })
        self.assertEqual(uploaded.status_code, 200)

        scheduled = self.client.post("/api/content/schedule", json={
            "account_id": account_id,
            "content": "عنوان الفيديو\nالوصف",
            "time": "23:59",
            "media_id": uploaded.json["media_id"],
        })

        self.assertEqual(scheduled.status_code, 200)
        post = db.list_scheduled_posts()[0]
        self.assertEqual(post["media_path"].rsplit("/", 1)[-1], uploaded.json["media_id"])

    def test_target_requires_an_existing_active_account(self):
        missing = self.client.post("/api/targets", json={"account_id": 999, "username": "target"})
        self.assertEqual(missing.status_code, 404)

        db.add_account("instagram", "sample", {"password": "secret"})
        account_id = db.list_accounts()[0]["id"]
        added = self.client.post("/api/targets", json={"account_id": account_id, "username": "target"})
        self.assertEqual(added.status_code, 200)

    def test_x_media_is_uploaded_and_attached_to_the_post(self):
        credentials = {
            "api_key": "key", "api_secret": "secret",
            "access_token": "token", "access_token_secret": "token-secret",
        }
        db.add_account("x", "sample", credentials)
        account = db.list_accounts()[0]

        with patch("platforms.x_twitter.tweepy.API") as api_class, patch("platforms.x_twitter.tweepy.Client") as client_class:
            api_class.return_value.media_upload.return_value.media_id_string = "media-123"
            from platforms.x_twitter import XPlatform
            adapter = XPlatform(account)
            adapter.publish("صورة اليوم", "/tmp/photo.jpg")

        api_class.return_value.media_upload.assert_called_once_with(filename="/tmp/photo.jpg")
        client_class.return_value.create_tweet.assert_called_once_with(
            text="صورة اليوم", media_ids=["media-123"]
        )

    def test_comment_cannot_be_approved_twice_through_api(self):
        comment_id = db.add_to_queue(1, "instagram", "target", "post-1", "", {}, "draft")

        first = self.client.post(f"/api/queue/{comment_id}/approve", json={})
        repeated = self.client.post(f"/api/queue/{comment_id}/approve", json={})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 409)

    def test_youtube_like_obeys_persistent_rate_limit(self):
        from types import SimpleNamespace
        from platforms.base import RateLimitExceeded
        from platforms.youtube import YouTubePlatform

        adapter = object.__new__(YouTubePlatform)
        adapter.like_limiter = SimpleNamespace(allow=lambda: False)
        with self.assertRaises(RateLimitExceeded):
            adapter.like("video-id")


if __name__ == "__main__":
    unittest.main()