"""مسارات قواعد الردود التلقائية وسجلها."""
import json
from urllib.parse import urlsplit
from flask import Blueprint, request, jsonify
from core import database as db
from core import keywords

bp = Blueprint("replies", __name__)


def _view(r):
    return {"id": r["id"], "account_id": r["account_id"], "name": r["name"], "media_id": r["media_id"],
            "keywords": json.loads(r["keywords"] or "[]"), "any_comment": bool(r["any_comment"]),
            "whole_word": bool(r["whole_word"]), "public_replies": json.loads(r["public_replies"] or "[]"),
            "dm_text": r["dm_text"], "dm_link": r["dm_link"], "active": bool(r["active"]),
            "channel": r["channel"] or "comment", "reaction": r["reaction"] or ""}


def _clean(data, partial=False):
    """يتحقق من الحقول. يعيد (القيم، رسالة الخطأ)."""
    out = {}
    if "keywords" in data or not partial:
        kws = [str(k).strip() for k in (data.get("keywords") or []) if str(k).strip()]
        out["keywords"] = kws[:30]
    if "public_replies" in data or not partial:
        out["public_replies"] = [str(r).strip()[:500] for r in (data.get("public_replies") or []) if str(r).strip()][:10]
    for key, limit in (("name", 80), ("dm_text", 1000)):
        if key in data or not partial:
            out[key] = str(data.get(key) or "").strip()[:limit]
    if "dm_link" in data or not partial:
        link = str(data.get("dm_link") or "").strip()
        if link and (urlsplit(link).scheme != "https" or not urlsplit(link).hostname):
            return None, "رابط الرسالة يجب أن يكون HTTPS صالحاً"
        out["dm_link"] = link
    for key in ("any_comment", "whole_word", "active"):
        if key in data:
            out[key] = bool(data[key])
    if "channel" in data or not partial:
        if str(data.get("channel") or "comment") not in ("comment", "dm"):
            return None, "القناة: comment أو dm فقط"
        out["channel"] = str(data.get("channel") or "comment")
    if "reaction" in data or not partial:
        if str(data.get("reaction") or "") not in ("", "love"):
            return None, "التفاعل المدعوم: love فقط"
        out["reaction"] = str(data.get("reaction") or "")
    if "media_id" in data:
        out["media_id"] = str(data["media_id"] or "").strip()
    return out, ""


@bp.get("/api/reply-rules")
def list_rules():
    return jsonify([_view(r) for r in db.list_reply_rules()])


@bp.post("/api/reply-rules")
def add_rule():
    data = request.get_json(force=True)
    acc = db.get_account(int(data.get("account_id") or 0))
    if not acc or not acc["active"] or acc["platform"] != "instagram":
        return jsonify({"error": "اختر حساب Instagram مفعّلاً"}), 400
    f, err = _clean(data)
    if err:
        return jsonify({"error": err}), 400
    if not f["keywords"] and not data.get("any_comment"):
        return jsonify({"error": "أضف كلمة مفتاحية أو فعّل الرد على كل تعليق"}), 400
    if not f["public_replies"] and not f["dm_text"] and not f["reaction"]:
        return jsonify({"error": "اكتب رداً علنياً أو رسالة خاصة"}), 400
    rid = db.add_reply_rule(acc["id"], f["name"], str(data.get("media_id") or "").strip(), f["keywords"],
                            data.get("any_comment", False), data.get("whole_word", True),
                            f["public_replies"], f["dm_text"], f["dm_link"], active=data.get("active", True) is True,
                            channel=f["channel"], reaction=f["reaction"])
    return jsonify({"ok": True, "id": rid})


@bp.patch("/api/reply-rules/<int:rule_id>")
def patch_rule(rule_id):
    f, err = _clean(request.get_json(force=True), partial=True)
    if err:
        return jsonify({"error": err}), 400
    if not db.update_reply_rule(rule_id, **f):
        return jsonify({"error": "القاعدة غير موجودة"}), 404
    return jsonify({"ok": True})


@bp.delete("/api/reply-rules/<int:rule_id>")
def delete_rule(rule_id):
    db.delete_reply_rule(rule_id)
    return jsonify({"ok": True})


@bp.post("/api/reply-rules/test")
def test_match():
    data = request.get_json(force=True)
    hit = keywords.match_keywords(str(data.get("text") or ""), [str(k) for k in (data.get("keywords") or [])],
                                  bool(data.get("whole_word", True)))
    return jsonify({"matched": hit is not None, "keyword": hit})


@bp.get("/api/reply-log")
def reply_log():
    return jsonify([{"id": r["id"], "rule_id": r["rule_id"], "username": r["username"], "comment": r["comment_text"],
                     "public_status": r["public_status"], "dm_status": r["dm_status"], "status": r["status"],
                     "public_text": r["public_text"], "dm_text": r["dm_text"], "kind": r["kind"], "reaction": r["reaction"],
                     "held_reason": r["held_reason"], "error": r["error"], "created_at": r["created_at"]} for r in db.list_reply_log()])


@bp.get("/api/replies/status")
def replies_status():
    from core import auto_reply
    return jsonify({"paused": auto_reply.paused(), "live": auto_reply.live(), "approval": auto_reply.needs_approval(),
                    "counts": db.reply_counts()})


@bp.post("/api/replies/pause")
def replies_pause():
    db.set_setting("replies_paused", "1")
    return jsonify({"ok": True, "paused": True})


@bp.post("/api/replies/resume")
def replies_resume():
    db.set_setting("replies_paused", "0")
    return jsonify({"ok": True, "paused": False})


@bp.post("/api/replies/live")
def replies_live():
    """تفعيل/إيقاف الإرسال الفعلي. التفعيل يحتاج confirm=true صريحاً."""
    data = request.get_json(silent=True) or {}
    on = data.get("enabled") is True
    if on and data.get("confirm") is not True:
        return jsonify({"error": "التفعيل يحتاج confirm=true بعد اجتياز قائمة الإطلاق"}), 400
    db.set_setting("replies_live", "1" if on else "0")
    return jsonify({"ok": True, "live": on})


@bp.post("/api/reply-log/<int:reply_id>/approve")
def approve(reply_id):
    if not db.decide_draft(reply_id, True):
        return jsonify({"error": "لا توجد مسودة بهذا الرقم، أو القاعدة/الحساب متوقف"}), 409
    return jsonify({"ok": True})


@bp.post("/api/reply-log/<int:reply_id>/reject")
def reject(reply_id):
    if not db.decide_draft(reply_id, False):
        return jsonify({"error": "لا توجد مسودة بهذا الرقم"}), 409
    return jsonify({"ok": True})


@bp.post("/api/reply-rules/preview")
def preview():
    """تجربة جافة بلا شبكة: ماذا سيُكتب لو وصل هذا التعليق. لا يحفظ شيئاً ولا يرسل."""
    from core import auto_reply
    data = request.get_json(force=True)
    c = {"comment_id": "preview", "media_id": str(data.get("media_id") or ""), "user_id": "0",
         "username": str(data.get("username") or "user"), "text": str(data.get("text") or ""), "ts": 0}
    rules = [r for r in db.list_reply_rules(active_only=True) if r["account_id"] == int(data.get("account_id") or 0)]
    rule = auto_reply.match_rule(rules, c)
    if not rule:
        return jsonify({"matched": False})
    public, dm = auto_reply.compose(rule, c)
    return jsonify({"matched": True, "rule_id": rule["id"], "public": public, "dm": dm})


@bp.post("/api/reply-log/<int:reply_id>/retry")
def retry(reply_id):
    data = request.get_json(silent=True) or {}
    confirmed = data.get("confirm_unknown") is True
    with db._conn() as c:
        cur = c.execute(
            "UPDATE reply_log SET status='queued', release_at=?, error='', "
            "public_status=CASE WHEN public_status IN ('failed','outcome_unknown') THEN 'pending' ELSE public_status END, "
            "dm_status=CASE WHEN dm_status IN ('failed','outcome_unknown') THEN 'pending' ELSE dm_status END "
            "WHERE id=? AND (public_status IN ('failed','outcome_unknown') OR dm_status IN ('failed','outcome_unknown')) "
            "AND (? OR (public_status!='outcome_unknown' AND dm_status!='outcome_unknown')) "
            "AND EXISTS(SELECT 1 FROM reply_rules r WHERE r.id=reply_log.rule_id AND r.active=1) "
            "AND EXISTS(SELECT 1 FROM accounts a WHERE a.id=reply_log.account_id AND a.active=1)",
            (__import__("time").time(), reply_id, confirmed))
        if cur.rowcount != 1:
            return jsonify({"error": "لا يوجد ما يُعاد"}), 409
    return jsonify({"ok": True})
