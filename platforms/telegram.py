"""تلغرام: عبر Bot API الرسمي. أنشئ بوتاً من @BotFather وأضفه مشرفاً في قناتك."""
import requests
from platforms.base import BasePlatform, NotSupported

CAPTION_MAX = 1024


class TelegramPlatform(BasePlatform):
    name = "telegram"
    required_fields = ["bot_token", "chat_id"]
    capabilities = {"text": True, "image": True, "video": True, "max_len": 4096}

    def _call(self, method, data=None, files=None, timeout=60):
        r = requests.post(f"https://api.telegram.org/bot{self.creds['bot_token']}/{method}",
                          data=data, files=files, timeout=timeout)
        r.raise_for_status()
        return r.json().get("result", {})

    def _send_media(self, method, field, text, media_path):
        caption = text if len(text) <= CAPTION_MAX else ""
        with open(media_path, "rb") as f:
            res = self._call(method, {"chat_id": self.creds["chat_id"], "caption": caption}, {field: f}, timeout=600)
        if text and not caption:
            self._call("sendMessage", {"chat_id": self.creds["chat_id"], "text": text})
        return str(res.get("message_id", ""))

    def publish(self, text, media_path=None):
        if media_path:
            return self._send_media("sendPhoto", "photo", text, media_path)
        return str(self._call("sendMessage", {"chat_id": self.creds["chat_id"], "text": text}).get("message_id", ""))

    def publish_video(self, text, media_path):
        return self._send_media("sendVideo", "video", text, media_path)

    def comment(self, post_id, text):
        raise NotSupported("تلغرام: التعليق غير مدعوم")

    def like(self, post_id):
        raise NotSupported("تلغرام: الإعجاب غير مدعوم")

    def recent_posts(self, username, limit=5):
        return []

    def stories(self, username=None):
        return []

    def probe(self):
        me = self._call("getMe", timeout=20)
        chat = self._call("getChat", {"chat_id": self.creds["chat_id"]}, timeout=20)
        return {"bot": me.get("username", ""), "chat": chat.get("title") or chat.get("username") or ""}
