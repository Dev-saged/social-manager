"""خادم الويب: REST API محلي تستهلكه واجهة HTML (static/index.html).

يعمل على 127.0.0.1 فقط: لا شيء من خارج الجهاز يصل إلى بياناتك.
WebToApp يشغّل هذا الملف كخادم بايثون محلي على الجهاز ويعرض الواجهة في WebView.
"""
import threading
import json
import os
import datetime
from flask import Flask, request, jsonify, send_from_directory
import config
from core import database as db
from core import interaction
from core import ai_engine

app = Flask(__name__, static_folder=None)
UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

# حالة بناء الطابور (تُقرأ من الواجهة بالاستقصاء)
_build_state = {"running": False, "progress": "", "added": 0, "error": ""}


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
    if platform not in ("instagram", "facebook", "x", "youtube") or not username:
        return jsonify({"error": "منصة غير صحيحة أو اسم مستخدم فارغ"}), 400
    db.add_account(platform, username, data.get("credentials") or {}, data.get("type", "personal"))
    return jsonify({"ok": True})


@app.delete("/api/accounts/<int:account_id>")
def delete_account(account_id):
    db.delete_account(account_id)
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
    media_path = data.get("media_path") or None
    time_str = (data.get("time") or "").strip()
    if not account_id or not content:
        return jsonify({"error": "اختر الحساب واكتب المحتوى"}), 400
    try:
        hh, mm = map(int, time_str.split(":"))
        now = datetime.datetime.now()
        when = now.replace(hour=hh, minute=mm, second=0)
        if when <= now:
            when += datetime.timedelta(days=1)
        db.schedule_post(account_id, content, media_path, when.timestamp())
        return jsonify({"ok": True, "at": when.strftime("%Y-%m-%d %H:%M")})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.get("/api/posts")
def list_posts():
    return jsonify([
        {"id": p["id"], "content": p["content"], "scheduled_at": p["scheduled_at"], "status": p["status"]}
        for p in db.list_scheduled_posts()
    ])


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
    interaction.approve(comment_id)
    return jsonify({"ok": True})


@app.post("/api/queue/<int:comment_id>/reject")
def reject(comment_id):
    interaction.reject(comment_id)
    return jsonify({"ok": True})


@app.post("/api/queue/<int:comment_id>/edit")
def edit_comment(comment_id):
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "النص فارغ"}), 400
    interaction.edit_and_approve(comment_id, text)
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
        db.set_setting("thaura_api_key", data["api_key"])
        import core.ai_engine as ai
        ai._client = None  # مفتاح جديد: أعِد بناء عميل الذكاء
    return jsonify({"ok": True})
