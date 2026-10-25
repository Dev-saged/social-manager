"""مستودع الوسائط: Instagram الرسمي لا يقبل رفع ملف من الجهاز، بل رابطاً عاماً.

يُرفع الملف مؤقتاً إلى مستودع GitHub عام تملكه أنت (رمزك أنت)، ويُعطى رابطه الخام لـ Meta،
ثم يُحذف بعد النشر. الصور تُحوَّل إلى JPEG لأن Instagram لا يقبل غيره.
"""
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
    """يرفع الملف ويعيد (الرابط العام، مقبض الحذف)."""
    if not configured():
        raise AppError("media_url_needed")
    s = settings()
    src, temp = (_as_jpeg(path) if kind == "image" else (path, False))
    try:
        ext = src.rsplit(".", 1)[-1].lower()
        name = f"relay/{uuid.uuid4().hex}.{ext}"
        with open(src, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        r = requests.put(f"{API}/repos/{s['repo']}/contents/{name}", headers=_headers(), timeout=300,
                         json={"message": "media relay", "content": content, "branch": s["branch"]})
        r.raise_for_status()
        data = r.json()["content"]
        return data["download_url"], {"path": name, "sha": data["sha"]}
    finally:
        if temp:
            try:
                os.remove(src)
            except OSError:
                pass


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
    """يتحقق من الرمز والمستودع (عام، ولديك صلاحية الكتابة)."""
    s = settings()
    r = requests.get(f"{API}/repos/{s['repo']}", headers=_headers(), timeout=20)
    r.raise_for_status()
    j = r.json()
    if j.get("private"):
        raise AppError("permission", "المستودع خاص؛ Meta لا تستطيع قراءة روابطه، اجعله عاماً")
    if not (j.get("permissions") or {}).get("push"):
        raise AppError("permission", "الرمز لا يملك صلاحية الكتابة (Contents: Read and write)")
    return {"repo": j.get("full_name", "")}
