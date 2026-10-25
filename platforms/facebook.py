"""فيسبوك: وضعان.

- share (حساب شخصي): Meta لا تسمح بالنشر الآلي على الملفات الشخصية منذ 2018، فيُجهَّز المنشور
  في موعده ويظهر لك «شارك الآن» فتنسخ الأداة النص وتفتح المشاركة وأنت تضغط نشر.
- personal (صفحة): عبر Meta Graph API (رسمي). يتطلب Page Access Token و page_id.

ملاحظة صريحة: Graph API يتيح النشر والتعليق على منشورات صفحتك أنت،
وجلب منشورات الصفحات المتابَعة (للمراقبة)، لكن لا يتيح التفاعل التلقائي مع حسابات الأصدقاء.
"""
import requests
import config
from core.errors import ManualShare
from platforms.base import BasePlatform, RateLimitExceeded, NotSupported

GRAPH = config.GRAPH_BASE


class FacebookPlatform(BasePlatform):
    name = "facebook"
    required_fields = ["access_token", "page_id"]
    capabilities = {"text": True, "image": True, "video": True, "max_len": 60000}

    def __init__(self, account_row):
        if account_row["account_type"] == "share":
            self.required_fields = []
        super().__init__(account_row)
        self.share = account_row["account_type"] == "share"

    def publish_media(self, text, media_path=None, kind=""):
        if self.share:
            raise ManualShare("فيسبوك الشخصي: افتح المنشور واضغط «شارك الآن»")
        return super().publish_media(text, media_path, kind)

    def _graph(self, endpoint, method="GET", **params):
        params["access_token"] = self.creds["access_token"]
        kw = {"params": params} if method == "GET" else {"data": params}
        r = requests.request(method, f"{GRAPH}/{endpoint}", timeout=30, **kw)
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

    def publish_video(self, text, media_path):
        with open(media_path, "rb") as f:
            r = requests.post(
                f"{GRAPH}/{self.creds['page_id']}/videos",
                data={"description": text, "access_token": self.creds["access_token"]},
                files={"source": f},
                timeout=600,
            )
        r.raise_for_status()
        return str(r.json().get("id", ""))

    def _page_comment(self, post_id, text):
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

    def probe(self):
        if self.share:
            return {"mode": "share"}
        return {"page": self._graph(f"{self.creds['page_id']}", fields="id,name").get("name", "")}

    def comment(self, post_id, text):
        if self.share:
            raise NotSupported("فيسبوك الشخصي: التعليق الآلي غير مسموح")
        return self._page_comment(post_id, text)
