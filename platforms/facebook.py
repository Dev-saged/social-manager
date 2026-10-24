"""فيسبوك: عبر Meta Graph API (رسمي). يتطلب Page Access Token و page_id.

ملاحظة صريحة: Graph API يتيح النشر والتعليق على منشورات صفحتك أنت،
وجلب منشورات الصفحات المتابَعة (للمراقبة)، لكن لا يتيح التفاعل التلقائي مع حسابات الأصدقاء.
"""
import requests
from platforms.base import BasePlatform, RateLimitExceeded

GRAPH = "https://graph.facebook.com/v21.0"


class FacebookPlatform(BasePlatform):
    name = "facebook"
    required_fields = ["access_token", "page_id"]

    def _graph(self, endpoint, method="GET", **params):
        params["access_token"] = self.creds["access_token"]
        r = requests.request(method, f"{GRAPH}/{endpoint}", data=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def publish(self, text, media_path=None):
        if media_path:
            # رفع صورة: مطلوب رابط عام أو رفع ثنائي؛ نستخدم الرفع الثنائي
            with open(media_path, "rb") as f:
                r = requests.post(
                    f"{GRAPH}/{self.creds['page_id']}/photos",
                    data={"caption": text, "access_token": self.creds["access_token"]},
                    files={"source": f},
                    timeout=60,
                )
            r.raise_for_status()
        else:
            self._graph(f"{self.creds['page_id']}/feed", "POST", message=text)

    def comment(self, post_id, text):
        if not self.comment_limiter.allow():
            raise RateLimitExceeded("تم تجاوز حد التعليقات لهذه الساعة")
        self._graph(f"{post_id}/comments", "POST", message=text)

    def like(self, post_id):
        if not self.like_limiter.allow():
            raise RateLimitExceeded("تم تجاوز حد الإعجابات لهذه الساعة")
        self._graph(f"{post_id}/likes", "POST")

    def recent_posts(self, username, limit=5):
        """يجلب منشورات صفحة متابَعة بالاسم (للمراقبة فقط، لا تفاعل تلقائي)."""
        try:
            pages = self._graph("search", q=username, type="page", fields="id")
            data = pages.get("data", [])
            if not data:
                return []
            page_id = data[0]["id"]
            res = self._graph(f"{page_id}/posts", fields="id,message,created_time", limit=limit)
            return [{"id": p["id"], "text": p.get("message", ""), "image_b64": None}
                    for p in res.get("data", [])]
        except Exception:
            return []

    def stories(self, username=None):
        return []  # حالات فيسبوك عبر API الرسمي غير متاحة للجلب

    def validate(self):
        try:
            self._graph(f"{self.creds['page_id']}?fields=id")
            return True
        except Exception:
            return False
