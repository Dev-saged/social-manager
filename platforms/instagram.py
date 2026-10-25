"""انستغرام: ثلاثة أوضاع، والرسمي هو الافتراضي.

- official: Instagram API with Instagram Login (رسمي، لحسابك الاحترافي Creator/Business، بلا صفحة فيسبوك).
  يتطلب access_token فقط؛ ig_user_id يُستنتج تلقائياً. الوسائط تحتاج رابطاً عاماً عبر مستودع الوسائط (core/relay).
  إن وُجدت كلمة مرور أو جلسة في نفس الحساب يُستخدم الوضع الشخصي بديلاً تلقائياً عند تعذّر الرسمي.
- business: Meta Graph API عبر فيسبوك (قديم، محفوظ كما هو).
- personal: مكتبة instagrapi (غير رسمية) بكلمة المرور أو بجلسة محفوظة (sessionid)، مع دعم رمز التأكيد والتحقق بخطوتين.
"""
import os
import threading
import time
import requests
from core import errors
from platforms.base import BasePlatform, NotSupported, RateLimitExceeded

GRAPH = "https://graph.facebook.com/v21.0"
IG_GRAPH = "https://graph.instagram.com"
FALLBACK_CODES = {"media_url_needed", "media_unsupported", "permission", "token_expired", "auth_invalid", "not_supported"}

# تسجيل دخول تفاعلي: الواجهة تستطلع الحالة وترسل الرمز
LOGINS = {}
_logins_lock = threading.Lock()


def login_state(account_id):
    with _logins_lock:
        st = LOGINS.get(account_id)
        return {k: v for k, v in st.items() if k != "event"} if st else {"state": "idle"}


def submit_code(account_id, code):
    with _logins_lock:
        st = LOGINS.get(account_id)
        if not st or st.get("state") != "need_code":
            return False
        st["code"] = str(code).strip()
        st["state"] = "verifying"
        st["event"].set()
        return True


def _set_login(account_id, **kw):
    with _logins_lock:
        LOGINS.setdefault(account_id, {"event": threading.Event()}).update(kw)


class InstagramPlatform(BasePlatform):
    name = "instagram"
    capabilities = {"text": False, "image": True, "video": True, "max_len": 2200}
    required_fields = []  # تُفحص حسب الوضع في __init__

    def __init__(self, account_row):
        super().__init__(account_row)
        self.mode = self.account["account_type"]  # official / personal / business
        self.interactive = False
        if self.mode == "business":
            missing = [f for f in ("access_token", "ig_user_id") if not self.creds.get(f)]
            if missing:
                raise ValueError(f"بيانات الدخول ناقصة: {', '.join(missing)}")
        elif self.mode == "official":
            if not self.creds.get("access_token"):
                raise ValueError("الوضع الرسمي يتطلب Access Token")
        elif not (self.creds.get("password") or self.creds.get("sessionid")):
            raise ValueError("الوضع الشخصي يتطلب كلمة المرور أو sessionid")
        self._cl = None  # عميل instagrapi على الطلب

    # ---------- أدوات Graph API (business) ----------
    def _graph(self, endpoint, method="GET", **params):
        params["access_token"] = self.creds["access_token"]
        kw = {"params": params} if method == "GET" else {"data": params}
        r = requests.request(method, f"{GRAPH}/{endpoint}", timeout=30, **kw)
        r.raise_for_status()
        return r.json()

    # ---------- الوضع الرسمي (graph.instagram.com) ----------
    def _ig(self, endpoint, method="GET", **params):
        params["access_token"] = self.creds["access_token"]
        kw = {"params": params} if method == "GET" else {"data": params}
        r = requests.request(method, f"{IG_GRAPH}/{endpoint}", timeout=60, **kw)
        r.raise_for_status()
        return r.json()

    def _uid(self):
        if not self.creds.get("ig_user_id"):
            me = self._ig("me", fields="user_id,username")
            self.creds["ig_user_id"] = str(me.get("user_id") or me.get("id"))
            self._persist()
        return self.creds["ig_user_id"]

    def _persist(self):
        from core import database as db
        db.update_account_credentials(self.account["id"], self.creds)

    def _wait_container(self, cid, video):
        """الفيديو يُعالج عند Meta قبل النشر: فحص كل 10 ثوانٍ حتى 5 دقائق."""
        for _ in range(30 if video else 6):
            st = self._ig(cid, fields="status_code").get("status_code", "FINISHED")
            if st == "FINISHED":
                return
            if st in ("ERROR", "EXPIRED"):
                raise errors.AppError("media_unsupported", f"Instagram رفض معالجة الملف ({st})")
            time.sleep(10 if video else 3)
        raise errors.AppError("server", "انتهت مهلة معالجة الفيديو عند Instagram")

    def _official_publish(self, text, media_path, kind):
        if not media_path:
            raise errors.AppError("invalid_input", "انستغرام يحتاج صورة أو فيديو")
        from core import relay
        url, handle = relay.upload(media_path, kind or "image")
        try:
            uid = self._uid()
            params = {"caption": text or ""}
            if kind == "video":
                params.update(media_type="REELS", video_url=url)
            else:
                params["image_url"] = url
            cid = self._ig(f"{uid}/media", "POST", **params)["id"]
            self._wait_container(cid, kind == "video")
            return str(self._ig(f"{uid}/media_publish", "POST", creation_id=cid).get("id", ""))
        finally:
            relay.remove(handle)

    def _has_fallback(self):
        if not (self.creds.get("password") or self.creds.get("sessionid")):
            return False
        import importlib.util
        return importlib.util.find_spec("instagrapi") is not None

    def needs_refresh(self, now):
        return self.mode == "official" and now - float(self.creds.get("refreshed_at") or 0) > 7 * 86400

    def refresh_token(self):
        """الرمز طويل الأمد يعيش 60 يوماً؛ يُجدَّد أسبوعياً (يشترط مرور 24 ساعة على إصداره)."""
        if self.mode != "official":
            return None
        r = requests.get(f"{IG_GRAPH}/refresh_access_token", timeout=20,
                         params={"grant_type": "ig_refresh_token", "access_token": self.creds["access_token"]})
        r.raise_for_status()
        self.creds = dict(self.creds, access_token=r.json()["access_token"], refreshed_at=time.time())
        return self.creds

    # ---------- الوضع الشخصي (instagrapi) ----------
    def _session_path(self):
        import config
        folder = os.path.join(config.DATA_DIR, "ig_sessions")
        os.makedirs(folder, mode=0o700, exist_ok=True)
        return os.path.join(folder, f"{self.account['id']}.json")

    def _wait_code(self, kind, message):
        """في تسجيل الدخول التفاعلي ينتظر رمزك من الواجهة حتى 5 دقائق، وفي الخلفية يفشل بسبب واضح."""
        code = "two_factor" if kind == "2fa" else "challenge_required"
        if not self.interactive:
            raise errors.AppError(code)
        aid = self.account["id"]
        _set_login(aid, state="need_code", kind=kind, message=message, code="")
        st = LOGINS[aid]
        st["event"].clear()
        if not st["event"].wait(300):
            _set_login(aid, state="failed", message="انتهت مهلة إدخال الرمز")
            raise errors.AppError(code, "انتهت مهلة إدخال الرمز")
        return st.get("code", "")

    def _login(self, cl):
        user = self.account["username"]
        if self.creds.get("sessionid") and not self.creds.get("password"):
            cl.login_by_sessionid(self.creds["sessionid"])
            return
        try:
            cl.login(user, self.creds["password"])
        except Exception as e:
            if type(e).__name__ != "TwoFactorRequired":
                raise
            code = self._wait_code("2fa", "أدخل رمز التحقق بخطوتين (من تطبيق المصادقة أو الرسالة)")
            cl.login(user, self.creds["password"], verification_code=code)

    def _personal_client(self):
        """الجلسة المحفوظة أولاً، فلا يُسجَّل الدخول من جديد إلا عند انتهائها (أقل تنبيهات أمنية)."""
        if self._cl is not None:
            return self._cl
        try:
            from instagrapi import Client
        except ImportError:
            raise RuntimeError("مكتبة instagrapi غير مثبتة على هذا الجهاز، Instagram الشخصي معطّل")
        cl = Client()
        cl.delay_range = [1, 3]
        cl.challenge_code_handler = lambda username, choice: self._wait_code(
            "challenge", f"أدخل الرمز الذي وصلك عبر {'البريد' if str(choice).endswith('EMAIL') else 'الرسائل'}")
        path = self._session_path()
        fresh = True
        if os.path.exists(path):
            try:
                cl.load_settings(path)
                cl.get_timeline_feed()
                fresh = False
            except Exception as e:
                if type(e).__name__ not in ("LoginRequired", "ClientError", "ClientLoginRequired", "KeyError", "ValueError", "JSONDecodeError"):
                    raise
                old = cl.get_settings()
                cl.set_settings({})
                if old.get("uuids"):
                    cl.set_uuids(old["uuids"])
        if fresh:
            self._login(cl)
        try:
            cl.dump_settings(path)
            os.chmod(path, 0o600)
        except OSError:
            pass
        self._cl = cl
        return cl

    def _personal_publish(self, text, media_path, kind):
        if kind == "video":
            from pathlib import Path
            media = self._personal_client().clip_upload(Path(media_path), text)
            return str(getattr(media, "pk", "") or "")
        if not media_path:
            raise ValueError("انستغرام الشخصي: النشر يحتاج صورة على الأقل")
        media = self._personal_client().photo_upload(media_path, text)
        return str(getattr(media, "pk", "") or "")

    # ---------- التنفيذ ----------
    def publish_media(self, text, media_path=None, kind=""):
        if self.mode == "official":
            try:
                return self._official_publish(text, media_path, kind)
            except Exception as e:
                if self._has_fallback() and errors.classify(e)["code"] in FALLBACK_CODES:
                    return self._personal_publish(text, media_path, kind)
                raise
        return super().publish_media(text, media_path, kind)

    def publish(self, text, media_path=None):
        if self.mode == "official":
            return self.publish_media(text, media_path, "image")
        if self.mode == "business":
            if not media_path:
                raise ValueError("النشر عبر Graph API يحتاج صورة/فيديو (caption يدعم النص)")
            media = self._graph(f"{self.creds['ig_user_id']}/media", "POST",
                                image_url=self.creds.get("public_media_url") or media_path,
                                caption=text)
            self._graph(f"{self.creds['ig_user_id']}/media_publish", "POST",
                        creation_id=media["id"])
        else:
            return self._personal_publish(text, media_path, "image")

    def publish_video(self, text, media_path):
        if self.mode == "official":
            return self.publish_media(text, media_path, "video")
        if self.mode == "business":
            raise NotSupported("Instagram Business يحتاج رابط فيديو عاماً، استخدم الوضع الرسمي أو الشخصي")
        return self._personal_publish(text, media_path, "video")

    # ---------- الردود التلقائية ----------
    def _graph_comments(self, fetch, uid, token_host, media_limit, per_media):
        import datetime
        out = []
        medias = fetch(f"{uid}/media", fields="id,comments_count", limit=media_limit).get("data", [])
        for m in medias:
            if not m.get("comments_count"):
                continue
            data = requests.get(f"{token_host}/{m['id']}/comments", timeout=30, params={
                "fields": "id,text,username,timestamp,from", "limit": per_media,
                "access_token": self.creds["access_token"]})
            data.raise_for_status()
            for c in data.json().get("data", []):
                if c.get("username") == self.account["username"]:
                    continue
                ts = datetime.datetime.fromisoformat(c["timestamp"].replace("+0000", "+00:00")).timestamp()
                out.append({"comment_id": c["id"], "media_id": m["id"], "user_id": str((c.get("from") or {}).get("id", "")),
                            "username": c.get("username", ""), "text": c.get("text", ""), "ts": ts})
        return out

    def recent_comments(self, media_limit=10, per_media=40):
        import datetime
        if self.mode == "business":
            return self._graph_comments(self._graph, self.creds["ig_user_id"], GRAPH, media_limit, per_media)
        if self.mode == "official":
            return self._graph_comments(self._ig, self._uid(), IG_GRAPH, media_limit, per_media)
        out = []
        cl = self._personal_client()
        me = str(cl.user_id)
        for m in cl.user_medias(me, amount=media_limit):
            if not m.comment_count:
                continue
            for c in cl.media_comments(m.pk, amount=per_media):
                if str(c.user.pk) == me:
                    continue
                ts = c.created_at_utc.replace(tzinfo=datetime.timezone.utc).timestamp()
                out.append({"comment_id": str(c.pk), "media_id": str(m.pk), "user_id": str(c.user.pk),
                            "username": c.user.username, "text": c.text or "", "ts": ts})
        return out

    def reply_to_comment(self, media_id, comment_id, text):
        if self.mode == "business":
            self._graph(f"{comment_id}/replies", "POST", message=text)
        elif self.mode == "official":
            self._ig(f"{comment_id}/replies", "POST", message=text)
        else:
            self._personal_client().media_comment(media_id, text, replied_to_comment_id=int(comment_id))

    def send_dm(self, user_id, text, comment_id=None):
        if self.mode in ("business", "official"):
            # Private Reply الرسمي: رد واحد لكل تعليق خلال 7 أيام
            host, uid = (GRAPH, self.creds["ig_user_id"]) if self.mode == "business" else (IG_GRAPH, self._uid())
            r = requests.post(f"{host}/{uid}/messages", timeout=30, params={
                "access_token": self.creds["access_token"]},
                json={"recipient": {"comment_id": comment_id}, "message": {"text": text}})
            r.raise_for_status()
        else:
            self._personal_client().direct_send(text, user_ids=[int(user_id)])

    def comment(self, post_id, text):
        if not self.comment_limiter.allow():
            raise RateLimitExceeded("تم تجاوز حد التعليقات لهذه الساعة")
        if self.mode == "business":
            self._graph(f"{post_id}/comments", "POST", message=text)
        elif self.mode == "official":
            self._ig(f"{post_id}/comments", "POST", message=text)
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
                "image_b64": None,
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

    def probe(self):
        if self.mode == "business":
            return {"id": self._graph(self.creds["ig_user_id"], fields="id,username").get("username", "")}
        if self.mode == "official":
            me = self._ig("me", fields="user_id,username,account_type")
            if not self.creds.get("ig_user_id"):
                self.creds["ig_user_id"] = str(me.get("user_id") or me.get("id"))
                self._persist()
            if self.creds.get("refreshed_at") is None:
                self.creds["refreshed_at"] = time.time()
                self._persist()
            return {"username": me.get("username", ""), "type": me.get("account_type", "")}
        cl = self._personal_client()
        return {"username": getattr(cl, "username", "") or self.account["username"]}
