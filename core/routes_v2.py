"""مسارات النشر المتعدد (مجموعات) والصحة. تُسجَّل في core/server.py."""
import datetime
import json
import time
from flask import Blueprint, request, jsonify
import config
from core import database as db
from core import media
from core import net
import platforms

bp = Blueprint("v2", __name__)


def _parse_when(value):
    """يقبل ثواني Unix أو نص ISO محلي مثل 2026-10-24T21:00. يعيد None إذا غير صالح."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return datetime.datetime.fromisoformat(value.strip()).timestamp()
        except ValueError:
            return None
    return None


def _state(group, targets):
    if group["status"] in ("draft", "cancelled"):
        return group["status"]
    st = {t["status"] for t in targets}
    if not st or st <= {"cancelled"}:
        return "cancelled"
    if st <= {"published", "cancelled"}:
        return "done"
    if "publishing" in st:
        return "publishing"
    if "waiting_network" in st:
        return "waiting"
    if st & {"published"} and st & {"failed", "missed"}:
        return "partial"
    if st <= {"failed", "missed", "cancelled"}:
        return "failed"
    return "scheduled"


def group_view(group):
    targets = db.group_targets(group["id"])
    names = {a["id"]: a["username"] for a in db.list_accounts()}
    try:
        meta = json.loads(group["meta"] or "{}")
    except ValueError:
        meta = {}
    return {
        "id": group["id"], "text": group["text"], "media_id": group["media_id"],
        "media_kind": group["media_kind"], "scheduled_at": group["scheduled_at"],
        "status": group["status"], "state": _state(group, targets),
        "source": group["source"], "meta": meta,
        "targets": [{
            "id": t["id"], "account_id": t["account_id"], "platform": t["platform"],
            "username": names.get(t["account_id"], ""), "status": t["status"],
            "attempts": t["attempts"], "error": t["error"], "text_override": t["text_override"],
            "next_try_at": t["next_try_at"],
        } for t in targets],
    }


def validate_targets(text, media_id, account_ids, overrides):
    """يتحقق من كل حساب مقابل إمكانيات منصته. يعيد (الأهداف، قائمة الأخطاء)."""
    caps = platforms.capabilities()
    kind = media.kind_of(media_id) if media_id else ""
    targets, errors = [], []
    for aid in account_ids:
        acc = db.get_account(aid)
        if not acc:
            errors.append(f"الحساب {aid} غير موجود")
            continue
        if not acc["active"]:
            errors.append(f"{acc['username']}: الحساب غير مفعّل")
            continue
        plat = acc["platform"]
        c = caps.get(plat)
        if not c:
            errors.append(f"{acc['username']}: منصة غير مدعومة")
            continue
        label = f"{plat} · {acc['username']}"
        if plat == "instagram" and acc["account_type"] == "business":
            errors.append(f"{label}: حساب Business يحتاج رابط وسائط عاماً، استخدم الجدولة السريعة")
            continue
        if not kind and not c["text"]:
            errors.append(f"{label}: لا يقبل منشوراً نصياً بدون صورة أو فيديو")
        if kind == "image" and not c["image"]:
            errors.append(f"{label}: لا يدعم الصور")
        if kind == "video" and not c["video"]:
            errors.append(f"{label}: لا يدعم الفيديو")
        own = (overrides.get(str(aid)) or overrides.get(aid) or "").strip()
        effective = own or text
        if len(effective) > c["max_len"]:
            errors.append(f"{label}: النص {len(effective)} حرفاً والحد {c['max_len']}")
        targets.append({"account_id": aid, "platform": plat, "text_override": own})
    return targets, errors


@bp.get("/api/health")
def health():
    return jsonify({"ok": True, "version": config.APP_VERSION, "online": net.online(),
                    "time": time.time(), "targets": db.target_counts()})


@bp.get("/api/platforms")
def platform_caps():
    return jsonify(platforms.capabilities())


@bp.post("/api/groups")
def create_group():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    media_id = (data.get("media_id") or "").strip()
    account_ids = [int(a) for a in (data.get("account_ids") or []) if str(a).isdigit()]
    overrides = data.get("overrides") or {}
    when = _parse_when(data.get("scheduled_at"))
    status = "draft" if data.get("draft") else "scheduled"
    if not account_ids:
        return jsonify({"error": "اختر منصة واحدة على الأقل"}), 400
    if not text and not media_id:
        return jsonify({"error": "اكتب نصاً أو أرفق ملفاً"}), 400
    if when is None:
        return jsonify({"error": "وقت النشر غير صالح"}), 400
    if when < time.time() - 60 and status == "scheduled":
        return jsonify({"error": "وقت النشر في الماضي"}), 400
    if media_id:
        import os
        if not media.valid_id(media_id) or not os.path.isfile(media.path_for(media_id)):
            return jsonify({"error": "الملف المرفوع غير موجود"}), 400
    targets, errors = validate_targets(text, media_id, account_ids, overrides if isinstance(overrides, dict) else {})
    if errors:
        return jsonify({"error": "؛ ".join(errors), "errors": errors}), 400
    gid = db.create_group(text, media_id, media.kind_of(media_id), when, targets, status=status,
                          source=data.get("source", "manual"), meta=data.get("meta") or {})
    return jsonify({"ok": True, "id": gid})


@bp.get("/api/groups")
def list_groups():
    since = _parse_when(request.args.get("from", type=float) or request.args.get("from"))
    until = _parse_when(request.args.get("to", type=float) or request.args.get("to"))
    return jsonify([group_view(g) for g in db.list_groups(since, until)])


@bp.patch("/api/groups/<int:group_id>")
def patch_group(group_id):
    data = request.get_json(force=True)
    g = db.get_group(group_id)
    if not g:
        return jsonify({"error": "المنشور غير موجود"}), 404
    when = _parse_when(data["scheduled_at"]) if "scheduled_at" in data else None
    if "scheduled_at" in data and when is None:
        return jsonify({"error": "وقت النشر غير صالح"}), 400
    status = None
    if data.get("approve") and g["status"] == "draft":
        status = "scheduled"
        if (when if when is not None else g["scheduled_at"]) < time.time():
            when = time.time() + 30
    text = data.get("text")
    if text is not None:
        text = text.strip()
    overrides = data.get("overrides") or {}
    effective = text if text is not None else g["text"]
    ids = [t["account_id"] for t in db.group_targets(group_id) if t["status"] != "cancelled"]
    merged = {str(t["account_id"]): t["text_override"] for t in db.group_targets(group_id)}
    merged.update({str(k): v for k, v in overrides.items()})
    _, errors = validate_targets(effective, g["media_id"], ids, merged)
    if errors:
        return jsonify({"error": "؛ ".join(errors), "errors": errors}), 400
    if not db.update_group(group_id, text=text, scheduled_at=when, status=status, overrides=overrides):
        return jsonify({"error": "لا يمكن تعديل منشور بدأ نشره أو أُلغي"}), 409
    return jsonify({"ok": True})


@bp.delete("/api/groups/<int:group_id>")
def delete_group(group_id):
    ok, media_id = db.cancel_group(group_id)
    if not ok:
        return jsonify({"error": "المنشور غير موجود"}), 404
    if media_id:
        media.delete(media_id)
        db.clear_group_media(group_id)
    return jsonify({"ok": True})


@bp.post("/api/group-targets/<int:target_id>/retry")
def retry_target(target_id):
    if not db.retry_target(target_id):
        return jsonify({"error": "لا يمكن إعادة محاولة هذا الهدف"}), 409
    return jsonify({"ok": True})


# ---------- الذكاء (Gemini) ----------
@bp.post("/api/ai/generate")
def ai_generate():
    from core import gemini
    data = request.get_json(force=True)
    mode = data.get("mode", "topic")
    tone = (data.get("tone") or "راقي وجذاب").strip()
    try:
        if mode == "news":
            titles = [g["meta"].get("title", "") for g in (group_view(x) for x in db.list_groups(time.time() - 3 * 86400, None)) if g["source"] == "ai_news"]
            return jsonify(gemini.tech_news([t for t in titles if t][-15:], tone))
        topic = (data.get("topic") or "").strip()
        if not topic:
            return jsonify({"error": "اكتب موضوع المنشور"}), 400
        return jsonify(gemini.write_post(topic, tone))
    except gemini.GeminiError as e:
        return jsonify({"error": str(e)}), 502


def _rule_view(r):
    return {"id": r["id"], "name": r["name"], "kind": r["kind"], "topic": r["topic"],
            "account_ids": json.loads(r["account_ids"] or "[]"), "times": json.loads(r["times"] or "[]"),
            "tone": r["tone"], "require_approval": bool(r["require_approval"]), "active": bool(r["active"]),
            "last_slot": r["last_slot"]}


def _rule_fields(data):
    kind = data.get("kind")
    if kind not in ("news", "topic"):
        return None, "نوع القاعدة: news أو topic"
    times = [t for t in (data.get("times") or []) if isinstance(t, str) and len(t) == 5 and t[2] == ":" and t[:2].isdigit() and t[3:].isdigit() and int(t[:2]) < 24 and int(t[3:]) < 60]
    if not times:
        return None, "حدّد وقتاً واحداً على الأقل بصيغة HH:MM"
    ids = [int(a) for a in (data.get("account_ids") or []) if str(a).isdigit()]
    if not ids:
        return None, "اختر حساباً واحداً على الأقل"
    if kind == "topic" and not (data.get("topic") or "").strip():
        return None, "اكتب موضوع القاعدة"
    return {"kind": kind, "times": times, "account_ids": ids}, ""


@bp.get("/api/rules")
def list_rules():
    return jsonify([_rule_view(r) for r in db.list_rules()])


@bp.post("/api/rules")
def add_rule():
    data = request.get_json(force=True)
    f, err = _rule_fields(data)
    if err:
        return jsonify({"error": err}), 400
    rid = db.add_rule((data.get("name") or "").strip(), f["kind"], (data.get("topic") or "").strip(),
                      f["account_ids"], f["times"], (data.get("tone") or "").strip(),
                      data.get("require_approval", True))
    return jsonify({"ok": True, "id": rid})


@bp.patch("/api/rules/<int:rule_id>")
def patch_rule(rule_id):
    data = request.get_json(force=True)
    allowed = {k: data[k] for k in ("name", "topic", "tone", "require_approval", "active", "account_ids", "times") if k in data}
    if not db.update_rule(rule_id, **allowed):
        return jsonify({"error": "القاعدة غير موجودة"}), 404
    return jsonify({"ok": True})


@bp.delete("/api/rules/<int:rule_id>")
def delete_rule(rule_id):
    db.delete_rule(rule_id)
    return jsonify({"ok": True})


@bp.post("/api/rules/<int:rule_id>/run")
def run_rule(rule_id):
    from core import auto_rules, gemini
    rule = db.get_rule(rule_id)
    if not rule:
        return jsonify({"error": "القاعدة غير موجودة"}), 404
    try:
        gid, title = auto_rules.generate_group(rule, time.time() + 60)
    except gemini.GeminiError as e:
        return jsonify({"error": str(e)}), 502
    db.mark_rule_run(rule_id, rule["last_slot"], title)
    return jsonify({"ok": True, "group_id": gid})
