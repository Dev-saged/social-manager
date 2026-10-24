"""توليد الصور والفيديو عبر Gemini (Oxion نصي فقط). كل توليد مهمة خلفية تستطلعها الواجهة."""
import base64
import os
import threading
import time
import uuid
import requests
import config
from core import database as db
from core import errors
from core import gemini
from core import media

IMAGE_DEFAULT = "gemini-3.1-flash-image"
VIDEO_DEFAULT = "gemini-omni-1.1-flash"
_jobs = {}
_lock = threading.Lock()


def _model(kind):
    chosen = db.get_setting(f"gemini_{kind}_model", "").strip()
    return chosen or gemini._recommended(kind) or (IMAGE_DEFAULT if kind == "image" else VIDEO_DEFAULT)


def _key():
    key = db.get_gemini_key(config.GEMINI_API_KEY)
    if not key:
        raise errors.AppError("auth_invalid", "توليد الصور والفيديو يحتاج مفتاح Gemini")
    return key


def _blocks(resp, kind):
    for step in resp.get("steps") or []:
        if step.get("type") == "model_output":
            for part in step.get("content") or []:
                if part.get("type") == kind:
                    yield part
    for out in resp.get("outputs") or []:
        if out.get("type") == kind:
            yield out


def _save(raw, ext):
    os.makedirs(media.media_dir(), mode=0o700, exist_ok=True)
    mid = f"{uuid.uuid4().hex}.{ext}"
    path = media.path_for(mid)
    with open(path, "wb") as f:
        f.write(raw)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return mid


def _download(uri):
    r = requests.get(uri if "alt=media" in uri else uri + ("&" if "?" in uri else "?") + "alt=media",
                     headers={"x-goog-api-key": _key()}, timeout=600)
    r.raise_for_status()
    return r.content


def generate(kind, prompt, aspect="9:16"):
    """يعيد معرّف ملف محلي جاهز للإرفاق. kind: image / video."""
    body = {"model": _model(kind), "input": prompt, "store": False}
    if kind == "image":
        body["response_format"] = {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": aspect}
    else:
        body["response_format"] = {"type": "video", "aspect_ratio": aspect if aspect in ("9:16", "16:9") else "9:16",
                                   "resolution": "720p"}
    r = requests.post(f"{gemini.API}/interactions", json=body, timeout=900,
                      headers={"x-goog-api-key": _key(), "Content-Type": "application/json"})
    r.raise_for_status()
    for part in _blocks(r.json(), kind):
        if part.get("data"):
            raw = base64.b64decode(part["data"])
        elif part.get("uri"):
            raw = _download(part["uri"])
        else:
            continue
        mime = part.get("mime_type", "")
        ext = "mp4" if kind == "video" else "png" if "png" in mime else "webp" if "webp" in mime else "jpg"
        return _save(raw, ext)
    raise errors.AppError("server", "لم يُرجع Gemini ملفاً")


def start(kind, prompt, aspect="9:16"):
    jid = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[jid] = {"state": "running", "kind": kind, "started": time.time()}

    def run():
        try:
            mid = generate(kind, prompt, aspect)
            upd = {"state": "done", "media_id": mid}
        except Exception as e:
            info = errors.classify(e)
            upd = {"state": "failed", "error": errors.text_of(info), "hint": info["hint"]}
            db.log("ai", f"gen_{kind}", upd["error"], ok=0)
        with _lock:
            _jobs[jid].update(upd)
    threading.Thread(target=run, daemon=True).start()
    return jid


def job(jid):
    with _lock:
        return dict(_jobs.get(jid) or {"state": "unknown"})
