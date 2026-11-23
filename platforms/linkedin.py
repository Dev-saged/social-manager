"""لينكدإن: النشر الرسمي على حسابك الشخصي عبر LinkedIn UGC Posts API.

تحتاج تطبيقاً من بوابة مطوّري لينكدإن مع منتج «Share on LinkedIn» (صلاحية w_member_social)
ومنتج «Sign In with LinkedIn using OpenID Connect» لمعرفة معرّفك تلقائياً.
الرمز يعيش نحو 60 يوماً؛ عند انتهائه أنشئ رمزاً جديداً وحدّث الحساب من الواجهة.
"""
import requests
from platforms.base import BasePlatform, NotSupported
from core.errors import AppError, DeliveryUncertain

API = "https://api.linkedin.com"


class LinkedInPlatform(BasePlatform):
    name = "linkedin"
    required_fields = ["access_token"]
    capabilities = {"text": True, "image": True, "video": False, "max_len": 3000}

    def _headers(self, json_body=True):
        h = {
            "Authorization": f"Bearer {self.creds['access_token']}",
            "X-Restli-Protocol-Version": "2.0.0",
        }
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _person_urn(self):
        urn = (self.creds.get("person_urn") or "").strip()
        if urn:
            return urn
        r = requests.get(f"{API}/v2/userinfo", headers=self._headers(json_body=False), timeout=30)
        r.raise_for_status()
        sub = (r.json() or {}).get("sub")
        if not sub:
            raise AppError("invalid_input", "لم يستطع لينكدإن إرجاع معرّفك؛ أضف منتج OpenID Connect أو اكتب person_urn يدوياً")
        return f"urn:li:person:{sub}"

    def _share_body(self, text, category, media=None):
        content = {
            "shareCommentary": {"text": text},
            "shareMediaCategory": category,
        }
        if media:
            content["media"] = media
        visibility = (self.creds.get("visibility") or "PUBLIC").strip().upper()
        if visibility not in ("PUBLIC", "CONNECTIONS"):
            visibility = "PUBLIC"
        return {
            "author": self._person_urn(),
            "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": content},
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": visibility},
        }

    def _upload_image(self, media_path):
        """يسجّل الصورة ويرفعها ويعيد URN الأصل. أي فشل هنا آمن لإعادة المحاولة (لم يُنشر شيء)."""
        owner = self._person_urn()
        r = requests.post(f"{API}/v2/assets?action=registerUpload", headers=self._headers(), timeout=30, json={
            "registerUploadRequest": {
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                "owner": owner,
                "serviceRelationships": [
                    {"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}
                ],
            }
        })
        r.raise_for_status()
        value = (r.json() or {}).get("value") or {}
        mechanism = value.get("uploadMechanism", {}).get(
            "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest", {})
        upload_url = mechanism.get("uploadUrl")
        asset = value.get("asset")
        if not upload_url or not asset:
            raise AppError("bad_request", "لم يُرجع لينكدإن رابط رفع الصورة")
        with open(media_path, "rb") as f:
            up = requests.post(upload_url, headers=self._headers(json_body=False), data=f, timeout=600)
        up.raise_for_status()
        return asset

    def publish(self, text, media_path=None):
        if not media_path:
            body = self._share_body(text, "NONE")
            r = requests.post(f"{API}/v2/ugcPosts", headers=self._headers(), json=body, timeout=30)
            r.raise_for_status()
            return str(r.headers.get("X-RestLi-Id", ""))

        asset = self._upload_image(media_path)
        body = self._share_body(text, "IMAGE", media=[{
            "status": "READY",
            "description": {"text": text[:200] if text else ""},
            "media": asset,
            "title": {"text": (text.split("\n", 1)[0] or "")[:200]},
        }])
        try:
            r = requests.post(f"{API}/v2/ugcPosts", headers=self._headers(), json=body, timeout=30)
            r.raise_for_status()
        except Exception as exc:
            raise DeliveryUncertain("رُفعت الصورة إلى لينكدإن ولم يتأكد إنشاء المنشور؛ افحص الحساب قبل أي إعادة إرسال") from exc
        return str(r.headers.get("X-RestLi-Id", ""))

    def publish_video(self, text, media_path):
        raise NotSupported("لينكدإن: نشر الفيديو غير مدعوم بعد، انشر نصاً أو صورة")

    def comment(self, post_id, text):
        raise NotSupported("لينكدإن: التعليق الآلي غير مدعوم")

    def like(self, post_id):
        raise NotSupported("لينكدإن: الإعجاب الآلي غير مدعوم")

    def recent_posts(self, username, limit=5):
        return []

    def stories(self, username=None):
        return []

    def probe(self):
        r = requests.get(f"{API}/v2/userinfo", headers=self._headers(json_body=False), timeout=20)
        r.raise_for_status()
        info = r.json() or {}
        return {"name": info.get("name", ""), "sub": info.get("sub", "")}
