"""Bluesky: عبر AT Protocol الرسمي. استخدم App Password من الإعدادات وليس كلمة مرورك الأساسية."""
import datetime
import re
import requests
from platforms.base import BasePlatform, NotSupported

PDS = "https://bsky.social/xrpc"
IMAGE_MAX_BYTES = 950_000
_LINK = re.compile(r"https?://[^\s]+")
_TAG = re.compile(r"(?<![\w#])#([^\s#.,!?؟،:;()]+)")


def build_facets(text):
    """روابط ووسوم قابلة للنقر. المواضع بالبايت (UTF-8) وليست بالحروف."""
    facets = []
    def span(m):
        start = len(text[:m.start()].encode("utf-8"))
        return {"byteStart": start, "byteEnd": start + len(m.group(0).encode("utf-8"))}
    for m in _LINK.finditer(text):
        facets.append({"index": span(m), "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group(0)}]})
    for m in _TAG.finditer(text):
        facets.append({"index": span(m), "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1)}]})
    return facets


class BlueskyPlatform(BasePlatform):
    name = "bluesky"
    required_fields = ["identifier", "app_password"]
    capabilities = {"text": True, "image": True, "video": False, "max_len": 300}

    def __init__(self, account_row):
        super().__init__(account_row)
        self._session = None

    def _auth(self):
        if self._session is None:
            r = requests.post(f"{PDS}/com.atproto.server.createSession", timeout=30,
                              json={"identifier": self.creds["identifier"], "password": self.creds["app_password"]})
            r.raise_for_status()
            self._session = r.json()
        return {"Authorization": f"Bearer {self._session['accessJwt']}"}

    def _upload(self, path):
        import mimetypes
        with open(path, "rb") as f:
            data = f.read()
        if len(data) > IMAGE_MAX_BYTES:
            raise ValueError("Bluesky: الصورة أكبر من 950 كيلوبايت")
        r = requests.post(f"{PDS}/com.atproto.repo.uploadBlob", data=data, timeout=60,
                          headers={**self._auth(), "Content-Type": mimetypes.guess_type(path)[0] or "image/jpeg"})
        r.raise_for_status()
        return r.json()["blob"]

    def publish(self, text, media_path=None):
        headers = self._auth()
        record = {"$type": "app.bsky.feed.post", "text": text, "langs": ["ar"],
                  "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")}
        facets = build_facets(text)
        if facets:
            record["facets"] = facets
        if media_path:
            record["embed"] = {"$type": "app.bsky.embed.images", "images": [{"alt": "", "image": self._upload(media_path)}]}
        r = requests.post(f"{PDS}/com.atproto.repo.createRecord", headers=headers, timeout=30,
                          json={"repo": self._session["did"], "collection": "app.bsky.feed.post", "record": record})
        r.raise_for_status()
        return r.json().get("uri", "")

    def comment(self, post_id, text):
        raise NotSupported("Bluesky: التعليق غير مدعوم")

    def like(self, post_id):
        raise NotSupported("Bluesky: الإعجاب غير مدعوم")

    def recent_posts(self, username, limit=5):
        return []

    def stories(self, username=None):
        return []

    def validate(self):
        try:
            self._auth()
            return True
        except Exception:
            return False
