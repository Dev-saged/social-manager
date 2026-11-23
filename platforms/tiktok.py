"""تيك توك: النشر الرسمي عبر TikTok Content Posting API (فيديو فقط).

قبل اجتياز تدقيق تيك توك لتطبيقك لا يُنشر إلا بوضع «خاص» (SELF_ONLY)؛
بعد التدقيق تتاح الخصوصية العامة من نفس الإعداد. الصور تحتاج نطاقاً موثقاً
فلا ندعمها هنا؛ الفيديو يُرفع مباشرة من الجهاز (FILE_UPLOAD).
"""
import os
import requests
from platforms.base import BasePlatform, NotSupported
from core.errors import AppError, DeliveryUncertain

API = "https://open.tiktokapis.com/v2"

_ERROR_CODES = {
    "access_token_invalid": "auth_invalid",
    "access_token_expired": "token_expired",
    "scope_not_authorized": "permission",
    "scope_permission_missed": "permission",
    "unaudited_client_can_only_post_to_private_accounts": "permission",
    "privacy_level_option_mismatch": "invalid_input",
    "rate_limit_exceeded": "rate_limited",
    "spam_risk_too_many_posts": "rate_limited",
    "spam_risk_user_banned_from_posting": "permission",
    "reached_active_user_cap": "rate_limited",
    "invalid_param": "invalid_input",
    "url_ownership_unverified": "permission",
}


class TikTokPlatform(BasePlatform):
    name = "tiktok"
    required_fields = ["access_token"]
    capabilities = {"text": False, "image": False, "video": True, "max_len": 2200}

    def _headers(self, json_body=True):
        h = {"Authorization": f"Bearer {self.creds['access_token']}"}
        if json_body:
            h["Content-Type"] = "application/json; charset=UTF-8"
        return h

    def _check_error(self, r):
        """تيك توك يعيد error.code داخل الجسم حتى مع نجاح HTTP."""
        r.raise_for_status()
        body = r.json() or {}
        err = body.get("error") or {}
        code = err.get("code", "ok")
        if code and code != "ok":
            raise AppError(_ERROR_CODES.get(code, "bad_request"),
                          f"tiktok:{code} - {err.get('message', '')[:200]}")
        return body.get("data") or {}

    def _privacy(self):
        level = (self.creds.get("privacy_level") or "SELF_ONLY").strip().upper()
        allowed = {"SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE"}
        return level if level in allowed else "SELF_ONLY"

    def publish(self, text, media_path=None):
        raise NotSupported("تيك توك يقبل الفيديو فقط؛ أرفق مقطع فيديو")

    def publish_video(self, text, media_path):
        size = os.path.getsize(media_path)
        if size <= 0:
            raise AppError("invalid_input", "ملف الفيديو فارغ")
        init_body = {
            "post_info": {
                "title": (text or "")[:self.capabilities["max_len"]],
                "privacy_level": self._privacy(),
                "brand_content_toggle": False,
                "brand_organic_toggle": False,
                "is_aigc": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,
                "total_chunk_count": 1,
            },
        }
        r = requests.post(f"{API}/post/publish/video/init/", headers=self._headers(),
                          json=init_body, timeout=60)
        data = self._check_error(r)
        upload_url, publish_id = data.get("upload_url"), data.get("publish_id", "")
        if not upload_url:
            raise AppError("bad_request", "لم يُرجع تيك توك رابط رفع الفيديو")

        ext = os.path.splitext(media_path)[1].lower()
        mime = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}.get(ext, "video/mp4")
        try:
            with open(media_path, "rb") as f:
                up = requests.put(upload_url, data=f, timeout=900, headers={
                    "Content-Type": mime,
                    "Content-Length": str(size),
                    "Content-Range": f"bytes 0-{size - 1}/{size}",
                })
            up.raise_for_status()
        except Exception as exc:
            raise DeliveryUncertain("بدأ رفع الفيديو إلى تيك توك ولم تتأكد النتيجة؛ افحص الحساب قبل أي إعادة إرسال") from exc
        return str(publish_id)

    def comment(self, post_id, text):
        raise NotSupported("تيك توك: التعليق الآلي غير مدعوم")

    def like(self, post_id):
        raise NotSupported("تيك توك: الإعجاب الآلي غير مدعوم")

    def recent_posts(self, username, limit=5):
        return []

    def stories(self, username=None):
        return []

    def probe(self):
        r = requests.post(f"{API}/post/publish/creator_info/query/", headers=self._headers(), timeout=30)
        data = self._check_error(r)
        return {
            "creator": data.get("creator_nickname", ""),
            "privacy_options": ", ".join(data.get("privacy_level_options") or []),
        }

    def needs_refresh(self, now):
        """رمز تيك توك يعيش 24 ساعة فقط؛ يُجدَّد يومياً إن توفرت بيانات التطبيق."""
        if not (self.creds.get("refresh_token") and self.creds.get("client_key") and self.creds.get("client_secret")):
            return False
        return now - float(self.creds.get("refreshed_at") or 0) > 20 * 3600

    def refresh_token(self):
        r = requests.post("https://open.tiktokapis.com/v2/oauth/token/", timeout=30,
                          headers={"Content-Type": "application/x-www-form-urlencoded"},
                          data={
                              "client_key": self.creds["client_key"],
                              "client_secret": self.creds["client_secret"],
                              "grant_type": "refresh_token",
                              "refresh_token": self.creds["refresh_token"],
                          })
        r.raise_for_status()
        body = r.json() or {}
        err = body.get("error")
        if err and err != "ok":
            raise AppError(_ERROR_CODES.get(err, "auth_invalid"),
                           f"tiktok:{err} - {str(body.get('error_description') or body.get('message') or '')[:200]}")
        if not body.get("access_token"):
            raise AppError("auth_invalid", "لم يُرجع تيك توك رمزاً جديداً")
        import time
        creds = dict(self.creds,
                     access_token=body["access_token"],
                     refresh_token=body.get("refresh_token") or self.creds["refresh_token"],
                     refreshed_at=time.time())
        self.creds = creds
        return creds
