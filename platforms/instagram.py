"""انستغرام: وضعان.

- business: Meta Graph API (رسمي وآمن) للنشر والتعليق على منشورات حسابك.
  يتطلب access_token و ig_user_id من تطبيق Meta Developer.
- personal: مكتبة instagrapi (غير رسمية) لجلب منشورات الأصدقاء والتفاعل معها.
  خطر الحظر موجود فعلياً، لذلك معدلاتها منخفضة جداً والتفاعل يمر دائماً بموافقتك اليدوية.
"""
import requests
from platforms.base import BasePlatform, RateLimitExceeded

GRAPH = "https://graph.facebook.com/v21.0"


class InstagramPlatform(BasePlatform):
    name = "instagram"
    required_fields = []  # تُفحص حسب الوضع في __init__

    def __init__(self, account_row):
        super().__init__(account_row)
        self.mode = self.account["account_type"]  # personal / business
        if self.mode == "business":
            missing = [f for f in ("access_token", "ig_user_id") if not self.creds.get(f)]
            if missing:
                raise ValueError(f"بيانات الدخول ناقصة: {', '.join(missing)}")
        elif not self.creds.get("password"):
            raise ValueError("الوضع الشخصي يتطلب كلمة المرور")
        self._cl = None  # عميل instagrapi على الطلب

    # ---------- أدوات Graph API (business) ----------
    def _graph(self, endpoint, method="GET", **params):
        params["access_token"] = self.creds["access_token"]
        r = requests.request(method, f"{GRAPH}/{endpoint}", data=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _personal_client(self):
        if self._cl is None:
            from instagrapi import Client
            self._cl = Client()
            self._cl.login(self.account["username"], self.creds["password"])
        return self._cl

    # ---------- التنفيذ ----------
    def publish(self, text, media_path=None):
        if self.mode == "business":
            if not media_path:
                raise ValueError("النشر عبر Graph API يحتاج صورة/فيديو (caption يدعم النص)")
            media = self._graph(f"{self.creds['ig_user_id']}/media", "POST",
                                image_url=self.creds.get("public_media_url") or media_path,
                                caption=text)
            self._graph(f"{self.creds['ig_user_id']}/media_publish", "POST",
                        creation_id=media["id"])
        else:
            cl = self._personal_client()
            if media_path:
                cl.photo_upload(media_path, text)
            else:
                raise ValueError("انستغرام الشخصي: النشر يحتاج صورة على الأقل (أو استخدم وضع business)")

    def comment(self, post_id, text):
        if not self.comment_limiter.allow():
            raise RateLimitExceeded("تم تجاوز حد التعليقات لهذه الساعة")
        if self.mode == "business":
            self._graph(f"{post_id}/comments", "POST", message=text)
        else:
            self._personal_client().media_comment(post_id, text)

    def like(self, post_id):
        if not self.like_limiter.allow():
            raise RateLimitExceeded("تم تجاوز حد الإعجابات لهذه الساعة")
        if self.mode != "personal":
            raise ValueError("الإعجاب متاح في الوضع الشخصي فقط (Graph API لا يوفره)")
        self._personal_client().media_like(post_id)

    def recent_posts(self, username, limit=5):
        """جلب منشورات الآخرين يعمل في الوضع الشخصي فقط."""
        if self.mode != "personal":
            return []
        cl = self._personal_client()
        user_id = cl.user_id_from_username(username)
        medias = cl.user_medias(user_id, amount=limit)
        posts = []
        for m in medias:
            posts.append({
                "id": m.id,
                "text": m.caption_text or "",
                "image_b64": None,  # يمكن رفع الصورة لاحقاً بتحويل m.thumbnail_url
            })
        return posts

    def stories(self, username=None):
        if self.mode != "personal":
            return []
        cl = self._personal_client()
        if username:
            user_id = cl.user_id_from_username(username)
            return cl.user_stories(user_id)
        return cl.get_reels_tray().get("REELS_TRAY", [])

    def validate(self):
        try:
            if self.mode == "business":
                self._graph(f"{self.creds['ig_user_id']}?fields=id")
            else:
                self._personal_client()
            return True
        except Exception:
            return False
