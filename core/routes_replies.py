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
            "dm_text": r["dm_text"], "dm_link": r["dm_link"], "active": bool(r["active"])}


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
    if not f["public_replies"] and not f["dm_text"]:
        return jsonify({"error": "اكتب رداً علنياً أو رسالة خاصة"}), 400
    rid = db.add_reply_rule(acc["id"], f["name"], str(data.get("media_id") or "").strip(), f["keywords"],
                            data.get("any_comment", False), data.get("whole_word", True),
                            f["public_replies"], f["dm_text"], f["dm_link"])
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
                     "error": r["error"], "created_at": r["created_at"]} for r in db.list_reply_log()])


@bp.post("/api/reply-log/<int:reply_id>/retry")
def retry(reply_id):
    with db._conn() as c:
        cur = c.execute(
            "UPDATE reply_log SET status='queued', release_at=?, error='', "
            "public_status=CASE WHEN public_status='failed' THEN 'pending' ELSE public_status END, "
            "dm_status=CASE WHEN dm_status='failed' THEN 'pending' ELSE dm_status END "
            "WHERE id=? AND (public_status='failed' OR dm_status='failed')", (__import__("time").time(), reply_id))
        if cur.rowcount != 1:
            return jsonify({"error": "لا يوجد ما يُعاد"}), 409
    return jsonify({"ok": True})
