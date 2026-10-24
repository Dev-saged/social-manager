"""خادم الويب: REST API محلي تستهلكه واجهة HTML (static/index.html).

يعمل على 127.0.0.1 فقط: لا شيء من خارج الجهاز يصل إلى بياناتك.
WebToApp يشغّل هذا الملف كخادم بايثون محلي على الجهاز ويعرض الواجهة في WebView.
"""
import threading
import json
import os
import datetime
import re
import uuid
from urllib.parse import urlsplit
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import config
from core import database as db
from core import interaction
from core import ai_engine
from platforms import platform_names

app = Flask(__name__, static_folder=None)
from core.routes_v2 import bp as _v2
app.register_blueprint(_v2)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
MEDIA_DIR = os.path.join(config.DATA_DIR, "media")
ALLOWED_MEDIA = {"jpg", "jpeg", "png", "webp", "mp4", "mov"}
ACCOUNT_FIELDS = {
    ("instagram", "personal"): {"password"},
    ("instagram", "business"): {"access_token", "ig_user_id"},
    ("facebook", "personal"): {"access_token", "page_id"},
    ("x", "personal"): {"api_key", "api_secret", "access_token", "access_token_secret"},
}

# حالة بناء الطابور (تُقرأ من الواجهة بالاستقصاء)
_build_state = {"running": False, "progress": "", "added": 0, "error": ""}


@app.before_request
def restrict_local_api():
    if not request.path.startswith("/api/"):
        return None
    host = urlsplit(f"//{request.host}").hostname
    allowed_hosts = {"127.0.0.1", "localhost", "::1"}
    allowed_hosts.update(filter(None, os.getenv("APP_ALLOWED_HOSTS", "").lower().split(",")))
    if host not in allowed_hosts:
        return jsonify({"error": "اسم مضيف غير مسموح"}), 403
    origin = request.headers.get("Origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or parsed.netloc.lower() != request.host.lower():
            return jsonify({"error": "مصدر الطلب غير مسموح"}), 403
    return None


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "حجم الملف يتجاوز الحد الأقصى (100 ميغابايت)"}), 413


@app.get("/")
def home():
    return send_from_directory(UI_DIR, "index.html")


# ---------- لوحة المعلومات ----------
@app.get("/api/dashboard")
def dashboard():
    stats = db.queue_stats()
    return jsonify({
        "accounts": len(db.list_accounts(active_only=True)),
        "pending": stats.get("pending", 0),
        "approved": stats.get("approved", 0),
        "posted": stats.get("posted", 0),
        "scheduled_posts": len([p for p in db.list_scheduled_posts() if p["status"] == "pending"]),
        "logs": [
            {"ts": r["ts"], "platform": r["platform"], "action": r["action"],
             "detail": r["detail"], "ok": r["ok"]}
            for r in db.recent_logs(30)
        ],
    })


# ---------- الحسابات ----------
@app.get("/api/accounts")
def list_accounts():
    return jsonify([
        {"id": a["id"], "platform": a["platform"], "username": a["username"],
         "type": a["account_type"], "active": bool(a["active"])}
        for a in db.list_accounts()
    ])


@app.post("/api/accounts")
def add_account():
    data = request.get_json(force=True)
    platform = (data.get("platform") or "").strip().lower()
    username = (data.get("username") or "").strip()
    account_type = (data.get("type") or "personal").strip().lower()
    if platform == "instagram" and account_type not in ("personal", "business"):
        return jsonify({"error": "نوع حساب Instagram غير صحيح"}), 400
    if platform != "instagram":
        account_type = "personal"
    if platform not in platform_names() or not username:
        return jsonify({"error": "منصة غير صحيحة أو اسم مستخدم فارغ"}), 400
    credentials = data.get("credentials") or {}
    if not isinstance(credentials, dict):
        return jsonify({"error": "بيانات الدخول غير صحيحة"}), 400
    required = ACCOUNT_FIELDS.get((platform, account_type), set())
    missing = sorted(field for field in required if not str(credentials.get(field, "")).strip())
    if missing:
        return jsonify({"error": f"الحقول المطلوبة: {', '.join(missing)}"}), 400
    db.add_account(platform, username, credentials, account_type)
    return jsonify({"ok": True})


@app.delete("/api/accounts/<int:account_id>")
def delete_account(account_id):
    db.delete_account(account_id)
    return jsonify({"ok": True})


@app.post("/api/accounts/<int:account_id>/active")
def set_account_active(account_id):
    data = request.get_json(force=True)
    account = db.get_account(account_id)
    if not account:
        return jsonify({"error": "حساب غير موجود"}), 404
    if not isinstance(data.get("active"), bool):
        return jsonify({"error": "حالة الحساب غير صحيحة"}), 400
    db.set_account_active(account_id, data["active"])
    return jsonify({"ok": True})


@app.post("/api/accounts/<int:account_id>/validate")
def validate_account(account_id):
    acc = db.get_account(account_id)
    if not acc:
        return jsonify({"error": "حساب غير موجود"}), 404
    try:
        from platforms import get as get_adapter
        ok = get_adapter(acc["platform"], acc).validate()
        return jsonify({"ok": ok, "detail": "بيانات الدخول صحيحة" if ok else "فشل التحقق"})
    except Exception as e:
        return jsonify({"ok": False, "detail": str(e)})


# ---------- الأهداف ----------
@app.get("/api/targets")
def list_targets():
    return jsonify([
        {"id": t["id"], "account_id": t["account_id"], "username": t["username"]}
        for t in db.list_targets()
    ])


@app.post("/api/targets")
def add_target():
    data = request.get_json(force=True)
    account_id = int(data.get("account_id") or 0)
    username = (data.get("username") or "").strip()
    if not account_id or not username:
        return jsonify({"error": "اختر حساباً واكتب اسم الهدف"}), 400
    account = db.get_account(account_id)
    if not account:
        return jsonify({"error": "الحساب غير موجود"}), 404
    if not account["active"]:
        return jsonify({"error": "فعّل الحساب قبل إضافة أهداف له"}), 400
    db.add_target(account_id, username)
    return jsonify({"ok": True})


@app.delete("/api/targets/<int:target_id>")
def delete_target(target_id):
    db.delete_target(target_id)
    return jsonify({"ok": True})


# ---------- المحتوى ----------
@app.post("/api/content/generate")
def generate_content():
    data = request.get_json(force=True)
    topic = (data.get("topic") or "").strip()
    platform = (data.get("platform") or "instagram").strip()
    tone = data.get("tone", "راقي وجذاب")
    if not topic:
        return jsonify({"error": "اكتب موضوع المنشور"}), 400
    try:
        return jsonify(ai_engine.generate_content(topic, platform, tone))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/content/schedule")
def schedule_content():
    data = request.get_json(force=True)
    account_id = int(data.get("account_id") or 0)
    content = (data.get("content") or "").strip()
    media_id = (data.get("media_id") or "").strip()
    media_url = (data.get("media_url") or "").strip()
    time_str = (data.get("time") or "").strip()
    if not account_id or not content:
        return jsonify({"error": "اختر الحساب واكتب المحتوى"}), 400
    try:
        account = db.get_account(account_id)
        if not account:
            return jsonify({"error": "الحساب غير موجود"}), 404
        if not account["active"]:
            return jsonify({"error": "فعّل الحساب قبل جدولة النشر"}), 400
        media_path = None
        media_ext = ""
        if media_id:
            match = re.fullmatch(r"([a-f0-9]{32})\.(jpg|jpeg|png|webp|mp4|mov)", media_id)
            if not match:
                return jsonify({"error": "معرّف الملف غير صالح"}), 400
            media_path = os.path.join(MEDIA_DIR, media_id)
            if not os.path.isfile(media_path):
                return jsonify({"error": "الملف المرفوع غير موجود"}), 400
            media_ext = match.group(2)
        if media_url:
            parsed_url = urlsplit(media_url)
            if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username:
                return jsonify({"error": "رابط الوسائط يجب أن يكون HTTPS صالحاً"}), 400
            if account["platform"] != "instagram" or account["account_type"] != "business":
                return jsonify({"error": "الرابط العام للوسائط مطلوب فقط لحساب Instagram Business"}), 400
            if os.path.splitext(parsed_url.path)[1].lower() not in (".jpg", ".jpeg"):
                return jsonify({"error": "Instagram Business يتطلب رابط صورة JPEG عام"}), 400
            media_path = media_url
        if account["platform"] == "instagram" and account["account_type"] == "business" and not media_url:
            return jsonify({"error": "Instagram Business يحتاج رابط صورة JPEG عام"}), 400
        if account["platform"] == "youtube" and media_ext not in ("mp4", "mov"):
            return jsonify({"error": "نشر YouTube يحتاج ملف فيديو MP4 أو MOV مرفوعاً"}), 400
        if account["platform"] == "instagram" and account["account_type"] == "personal" and media_ext not in ("jpg", "jpeg", "png", "webp"):
            return jsonify({"error": "Instagram الشخصي يدعم رفع الصور فقط حالياً"}), 400
        if account["platform"] == "facebook" and media_ext not in ("", "jpg", "jpeg", "png", "webp"):
            return jsonify({"error": "Facebook يدعم المنشورات النصية أو الصور فقط حالياً"}), 400
        if account["platform"] == "x" and media_ext not in ("", "jpg", "jpeg", "png", "webp"):
            return jsonify({"error": "X يدعم المنشورات النصية أو الصور فقط حالياً"}), 400
        hh, mm = map(int, time_str.split(":"))
        now = datetime.datetime.now()
        when = now.replace(hour=hh, minute=mm, second=0)
        if when <= now:
            when += datetime.timedelta(days=1)
        db.schedule_post(account_id, content, media_path, when.timestamp())
        return jsonify({"ok": True, "at": when.strftime("%Y-%m-%d %H:%M")})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/media")
def upload_media():
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "اختر ملفاً أولاً"}), 400
    filename = secure_filename(uploaded.filename)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in ALLOWED_MEDIA:
        return jsonify({"error": "الصيغ المدعومة: JPG, PNG, WEBP, MP4, MOV"}), 400
    os.makedirs(MEDIA_DIR, mode=0o700, exist_ok=True)
    media_id = f"{uuid.uuid4().hex}.{extension}"
    path = os.path.join(MEDIA_DIR, media_id)
    uploaded.save(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return jsonify({"media_id": media_id, "name": filename, "extension": extension})


@app.get("/api/posts")
def list_posts():
    return jsonify([
        {"id": p["id"], "account_id": p["account_id"], "content": p["content"], "scheduled_at": p["scheduled_at"], "status": p["status"]}
        for p in db.list_scheduled_posts()
    ])


@app.delete("/api/posts/<int:post_id>")
def delete_post(post_id):
    if not db.delete_scheduled_post(post_id):
        return jsonify({"error": "المنشور غير موجود أو بدأ نشره بالفعل"}), 404
    return jsonify({"ok": True})


# ---------- طابور التفاعل ----------
@app.post("/api/queue/build")
def build_queue():
    if _build_state["running"]:
        return jsonify({"ok": True, "running": True})
    _build_state.update(running=True, progress="بدء الجلب...", added=0, error="")

    def work():
        try:
            n = interaction.build_queue(on_progress=lambda m: _build_state.update(progress=m))
            _build_state.update(added=n, progress=f"انتهى: {n} اقتراح جديد")
        except Exception as e:
            _build_state.update(error=str(e), progress="")
        finally:
            _build_state["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True, "running": True})


@app.get("/api/queue/build")
def build_status():
    return jsonify(_build_state)


@app.get("/api/queue")
def queue():
    out = []
    for c in db.pending_comments():
        try:
            analysis = dict(json.loads(c["analysis"])) if c["analysis"] else {}
        except Exception:
            analysis = {}
        out.append({
            "id": c["id"], "platform": c["platform"], "target": c["target_username"],
            "post_text": c["post_text"], "comment": c["proposed_comment"],
            "topic": analysis.get("topic", ""), "tone": analysis.get("tone", ""),
        })
    return jsonify(out)


@app.post("/api/queue/<int:comment_id>/approve")
def approve(comment_id):
    if not interaction.approve(comment_id):
        return jsonify({"error": "الاقتراح غير موجود أو تمت معالجته مسبقاً"}), 409
    return jsonify({"ok": True})


@app.post("/api/queue/<int:comment_id>/reject")
def reject(comment_id):
    if not interaction.reject(comment_id):
        return jsonify({"error": "الاقتراح غير موجود أو تمت معالجته مسبقاً"}), 409
    return jsonify({"ok": True})


@app.post("/api/queue/<int:comment_id>/edit")
def edit_comment(comment_id):
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "النص فارغ"}), 400
    if not interaction.edit_and_approve(comment_id, text):
        return jsonify({"error": "الاقتراح غير موجود أو تمت معالجته مسبقاً"}), 409
    return jsonify({"ok": True})


# ---------- الإعدادات ----------
@app.get("/api/settings")
def get_settings():
    return jsonify({
        "persona": db.get_setting("persona", config.DEFAULT_PERSONA),
        "has_api_key": bool(db.get_setting("thaura_api_key", "") or config.THAURA_API_KEY),
    })


@app.post("/api/settings")
def save_settings():
    data = request.get_json(force=True)
    if "persona" in data:
        db.set_setting("persona", data["persona"])
    if data.get("api_key"):
        db.set_api_key(data["api_key"])
        import core.ai_engine as ai
        ai._client = None  # مفتاح جديد: أعِد بناء عميل الذكاء
    return jsonify({"ok": True})
