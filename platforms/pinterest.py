"""بنترست: النشر الرسمي عبر Pinterest API v5 (صور فقط).

في وضع التجربة (Trial) تظهر الدبابيس لك وحدك حتى تُرقّى إلى الوصول القياسي (Standard)
من لوحة مطوّري بنترست. تحتاج لوحة (Board) ورمزاً بصلاحيات boards:read و boards:write
و pins:read و pins:write.
"""
import base64
import os
import requests
from platforms.base import BasePlatform, NotSupported
from core.errors import AppError

API = "https://api.pinterest.com/v5"

TITLE_MAX = 100
DESC_MAX = 800


class PinterestPlatform(BasePlatform):
    name = "pinterest"
    required_fields = ["access_token", "board_id"]
    capabilities = {"text": False, "image": True, "video": False, "max_len": DESC_MAX}

    def _headers(self):
        return {"Authorization": f"Bearer {self.creds['access_token']}",
                "Content-Type": "application/json"}

    def _board(self):
        board = (self.creds.get("board_id") or "").strip()
        if not board:
            raise AppError("invalid_input", "معرّف اللوحة (board_id) ناقص")
        return board

    def publish(self, text, media_path=None):
        if not media_path:
            raise NotSupported("بنترست يتطلب صورة لكل دبوس؛ أرفق صورة")
        ext = os.path.splitext(media_path)[1].lower()
        content_type = "image/png" if ext == ".png" else "image/jpeg"
        with open(media_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        title = (text or "").split("\n", 1)[0].strip()[:TITLE_MAX]
        body = {
            "board_id": self._board(),
            "description": (text or "")[:DESC_MAX],
            "media_source": {
                "source_type": "image_base64",
                "content_type": content_type,
                "data": data,
            },
        }
        if title:
            body["title"] = title
        r = requests.post(f"{API}/pins", headers=self._headers(), json=body, timeout=120)
        r.raise_for_status()
        return str((r.json() or {}).get("id", ""))

    def publish_video(self, text, media_path):
        raise NotSupported("بنترست: نشر الفيديو غير مدعوم بعد، انشر صورة")

    def comment(self, post_id, text):
        raise NotSupported("بنترست: التعليق الآلي غير مدعوم")

    def like(self, post_id):
        raise NotSupported("بنترست: الإعجاب الآلي غير مدعوم")

    def recent_posts(self, username, limit=5):
        return []

    def stories(self, username=None):
        return []

    def probe(self):
        r = requests.get(f"{API}/user_account", headers=self._headers(), timeout=20)
        r.raise_for_status()
        account = r.json() or {}
        rb = requests.get(f"{API}/boards/{self._board()}", headers=self._headers(), timeout=20)
        rb.raise_for_status()
        board = rb.json() or {}
        return {"username": account.get("username", ""), "board": board.get("name", "")}

    def needs_refresh(self, now):
        """رمز بنترست يعيش نحو 30 يوماً؛ يُجدَّد قبل انتهائه إن توفرت بيانات التطبيق."""
        if not (self.creds.get("refresh_token") and self.creds.get("app_id") and self.creds.get("app_secret")):
            return False
        return now - float(self.creds.get("refreshed_at") or 0) > 25 * 86400

    def refresh_token(self):
        r = requests.post(f"{API}/oauth/token", timeout=30,
                          auth=(self.creds["app_id"], self.creds["app_secret"]),
                          data={"grant_type": "refresh_token",
                                "refresh_token": self.creds["refresh_token"]})
        r.raise_for_status()
        body = r.json() or {}
        if not body.get("access_token"):
            raise AppError("auth_invalid", "لم يُرجع بنترست رمزاً جديداً")
        import time
        creds = dict(self.creds,
                     access_token=body["access_token"],
                     refresh_token=body.get("refresh_token") or self.creds["refresh_token"],
                     refreshed_at=time.time())
        self.creds = creds
        return creds
