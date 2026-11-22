"""مستودع GitHub العام متوقف لحماية الخصوصية. بديل التخزين الخاص منفصل وغير مفعّل."""
import base64
import os
import tempfile
import uuid
import requests
from core import database as db
from core.errors import AppError

API = "https://api.github.com"


def settings():
    return {"repo": db.get_setting("relay_repo", "").strip(), "branch": db.get_setting("relay_branch", "main").strip() or "main"}


def configured():
    s = settings()
    return bool(s["repo"] and "/" in s["repo"] and db.get_secret("github_token"))


def _headers():
    return {"Authorization": "Bearer " + db.get_secret("github_token"), "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _as_jpeg(path):
    """يعيد (مسار JPEG، هل هو مؤقت). يستخدم Pillow إن توفر (يأتي مع instagrapi)."""
    ext = path.rsplit(".", 1)[-1].lower()
    if ext in ("jpg", "jpeg"):
        return path, False
    try:
        from PIL import Image
    except ImportError:
        raise AppError("media_unsupported", "Instagram الرسمي يقبل صور JPG فقط؛ حوّل الصورة إلى JPG أو ثبّت مكتبة instagrapi")
    fd, out = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    with Image.open(path) as im:
        im.convert("RGB").save(out, "JPEG", quality=92)
    return out, True


def upload(path, kind="image"):
    """أوقف الرفع العام: حذف ملف GitHub لا يمحوه من سجل المستودع."""
    raise AppError("relay_disabled")


def remove(handle):
    if not handle:
        return
    s = settings()
    try:
        requests.delete(f"{API}/repos/{s['repo']}/contents/{handle['path']}", headers=_headers(), timeout=60,
                        json={"message": "media relay cleanup", "sha": handle["sha"], "branch": s["branch"]})
    except requests.RequestException:
        pass


def probe():
    """لا نفحص صلاحية رفع أوقفناه لحماية الوسائط."""
    raise AppError("relay_disabled")
