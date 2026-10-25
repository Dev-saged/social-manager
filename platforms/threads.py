"""Threads: عبر Threads API الرسمي (نصوص). الصور والفيديو تتطلب رابطاً عاماً فلا نعرضها هنا.

تحتاج توكن طويل الأمد ومعرّف مستخدم Threads من تطبيق Meta Developer.
"""
import requests
from platforms.base import BasePlatform, NotSupported

GRAPH = "https://graph.threads.net/v1.0"


class ThreadsPlatform(BasePlatform):
    name = "threads"
    required_fields = ["access_token", "user_id"]
    capabilities = {"text": True, "image": False, "video": False, "max_len": 500}

    def _post(self, endpoint, **params):
        params["access_token"] = self.creds["access_token"]
        r = requests.post(f"{GRAPH}/{endpoint}", data=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def publish(self, text, media_path=None):
        if media_path:
            raise NotSupported("Threads: الوسائط تتطلب رابطاً عاماً، انشر نصاً فقط")
        uid = self.creds["user_id"]
        container = self._post(f"{uid}/threads", media_type="TEXT", text=text)
        return str(self._post(f"{uid}/threads_publish", creation_id=container["id"]).get("id", ""))

    def comment(self, post_id, text):
        raise NotSupported("Threads: التعليق غير مدعوم")

    def like(self, post_id):
        raise NotSupported("Threads: الإعجاب غير مدعوم")

    def recent_posts(self, username, limit=5):
        return []

    def stories(self, username=None):
        return []

    def probe(self):
        r = requests.get(f"{GRAPH}/me", params={"access_token": self.creds["access_token"], "fields": "id,username"}, timeout=20)
        r.raise_for_status()
        return {"username": r.json().get("username", "")}

    def needs_refresh(self, now):
        return now - float(self.creds.get("refreshed_at") or 0) > 7 * 86400

    def refresh_token(self):
        """رمز Threads طويل الأمد يعيش 60 يوماً ويُجدَّد قبل انتهائه."""
        r = requests.get(f"{GRAPH.rsplit('/', 1)[0]}/refresh_access_token", timeout=20,
                         params={"grant_type": "th_refresh_token", "access_token": self.creds["access_token"]})
        r.raise_for_status()
        import time
        creds = dict(self.creds, access_token=r.json()["access_token"], refreshed_at=time.time())
        self.creds = creds
        return creds
