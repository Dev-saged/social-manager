"""الردود التلقائية: تعليق فيه كلمتك المفتاحية يستقبل رداً علنياً ورسالة خاصة.

لا Webhook هنا (التطبيق على جوالك): نفحص التعليقات كل دقيقتين. كل رد يُحجز قبل إرساله
ليمنع الإرسال المتزامن؛ ما كانت نتيجته غير مؤكدة يتوقف حتى تفحص الحساب وتؤكد إعادة الإرسال.
"""
import json
import random
import time
import config
from core import database as db
from core import errors
from core import keywords
from core import net
from platforms import get as get_adapter
from platforms.base import RateLimitExceeded

_last_poll = {}
PRIVATE_REPLY_WINDOW = 7 * 86400 - 3600  # ميتا تسمح بالرسالة الخاصة خلال 7 أيام من التعليق، نترك ساعة هامش


DM_WINDOW = 86400 - 3600  # الرد على رسالة واردة مسموح خلال 24 ساعة من رسالة الشخص
# مواضيع حساسة: تبقى مسودة دائماً حتى لو عُطّلت الموافقة العامة (أطفال، خصوصية، شكوى أو تهديد، مال)
_SENSITIVE = ("طفل", "اطفال", "ابني", "بنتي", "ولدي", "قاصر", "child", "kid", "minor",
              "عنوان", "رقم هاتف", "رقمي", "واتس", "بياناتي", "كلمه السر", "password", "otp",
              "شكوي", "تهديد", "نصب", "احتيال", "حقوق", "قانون", "scam", "fraud", "report", "lawyer",
              "ادفع", "حواله", "تحويل", "بطاقه", "refund", "payment")


def sensitive_reason(text):
    t = keywords._clean(text or "")
    for w in _SENSITIVE:
        if keywords._clean(w) in t:
            return w
    return ""


def paused():
    """مفتاح الإيقاف الطارئ: يوقف الفحص والإرسال فوراً دون حذف شيء."""
    return db.get_setting("replies_paused", "0") == "1"


def live():
    """الإرسال الفعلي مغلق افتراضياً: المسودات والمعتمدات تنتظر حتى يُفعَّل صراحةً."""
    return db.get_setting("replies_live", "0") == "1"


def needs_approval():
    """الافتراضي: كل رد يبقى مسودة حتى تعتمده (القيمة '0' في الإعداد reply_approval تعطّل ذلك)."""
    return db.get_setting("reply_approval", "1") != "0"


def match_rule(rules, comment):
    """أول قاعدة تنطبق (القواعد الخاصة بمنشور تسبق العامة). يعيد القاعدة أو None."""
    for rule in rules:
        if (rule["channel"] or "comment") != comment.get("kind", "comment"):
            continue
        if rule["media_id"] and rule["media_id"] != comment["media_id"]:
            continue
        if rule["any_comment"] or keywords.match_keywords(
                comment["text"], json.loads(rule["keywords"] or "[]"), bool(rule["whole_word"])):
            return rule
    return None


def compose(rule, comment):
    replies = [r.strip() for r in json.loads(rule["public_replies"] or "[]") if r.strip()]
    public = random.choice(replies).replace("{name}", "@" + comment["username"]) if replies else ""
    dm = (rule["dm_text"] or "").strip().replace("{name}", comment["username"])
    if dm and rule["dm_link"]:
        dm = f"{dm}\n{rule['dm_link']}"
    return public, dm


def poll(now=None):
    """يجلب التعليقات الجديدة ويجدول الرد على المطابق منها. يعيد عدد ما أُضيف."""
    now = time.time() if now is None else now
    added = 0
    if paused():
        return 0
    by_account = {}
    for rule in db.list_reply_rules(active_only=True):
        by_account.setdefault(rule["account_id"], []).append(rule)
    for account_id, rules in by_account.items():
        if now - _last_poll.get(account_id, 0) < config.REPLY_POLL_SECONDS:
            continue
        _last_poll[account_id] = now
        acc = db.get_account(account_id)
        if not acc or not acc["active"]:
            continue
        try:
            adapter = get_adapter(acc["platform"], acc)
            comments = [dict(c, kind="comment") for c in adapter.recent_comments()]
            if any((r["channel"] or "comment") == "dm" for r in rules) and hasattr(adapter, "recent_dms"):
                comments += [dict(c, kind="dm") for c in adapter.recent_dms()]
        except Exception as e:
            db.log(acc["platform"], "reply_poll", str(e), ok=0)
            continue
        for c in sorted(comments, key=lambda x: x["ts"]):
            if db.seen_comment(account_id, c["comment_id"]):
                continue
            rule = match_rule(rules, c)
            if not rule or c["ts"] < rule["created_at"]:
                continue
            is_dm = c.get("kind") == "dm"
            if now - c["ts"] > (DM_WINDOW if is_dm else PRIVATE_REPLY_WINDOW) or c.get("owner_replied"):
                continue
            if is_dm:
                if db.recent_reply_to_user(account_id, c["user_id"], "dm", now - config.REPLY_DM_COOLDOWN):
                    continue
            elif db.user_replied_on_media(account_id, c["media_id"], c["user_id"]):
                continue
            public, dm = compose(rule, c)
            reaction = rule["reaction"] or ""
            if is_dm:
                public = ""
            if not (public or dm or reaction):
                continue
            held = sensitive_reason(c["text"])
            release = now + random.uniform(*config.REPLY_DELAY_RANGE)
            if db.queue_reply(rule["id"], account_id, c, public, dm, release, "draft" if (held or needs_approval()) else "queued",
                             reaction, held):
                added += 1
    return added


def _attempt(reply, part, adapter):
    """يرسل جزءاً واحداً. 'sending' يُسجَّل قبل الإرسال حتى لا يتكرر. يعيد False إن أُجّل."""
    limiter = adapter.reply_limiter if part == "public" else adapter.dm_limiter
    if not limiter.allow():
        db.defer_reply_part(reply["id"], part)
        return False
    if not db.claim_reply_part(reply["id"], part):
        return False
    try:
        if part == "public":
            adapter.reply_to_comment(reply["media_id"], reply["comment_id"], reply["public_text"])
        else:
            is_dm = reply["kind"] == "dm"
            if reply["reaction"] and is_dm:
                adapter.react_to_message(reply["user_id"], reply["comment_id"], reply["reaction"])
            if reply["dm_text"]:
                adapter.send_dm(reply["user_id"], reply["dm_text"], None if is_dm else reply["comment_id"])
        db.set_reply_part(reply["id"], part, "sent")
        db.log(adapter.name, "reply_" + part, f"@{reply['username']}")
    except RateLimitExceeded as e:
        db.defer_reply_part(reply["id"], part, str(e))
        return False
    except Exception as e:
        info = errors.classify(e)
        uncertain = info["code"] in ("network", "server", "unknown", "outcome_unknown")
        state = "outcome_unknown" if uncertain else "failed"
        message = errors.text_of(errors.classify(errors.AppError("outcome_unknown"))) if uncertain else errors.text_of(info)
        db.set_reply_part(reply["id"], part, state, message)
        db.log(adapter.name, "reply_" + part, str(e), ok=0)
    return True


def release(now=None):
    now = time.time() if now is None else now
    db.recover_stuck_replies(now)
    if paused() or not live() or not net.online():
        return 0
    sent = 0
    for reply in db.due_replies(now):
        if db.sent_since(now - 86400) >= config.REPLY_DAILY_CAP:
            break
        acc = db.get_account(reply["account_id"])
        if not acc or not acc["active"]:
            continue
        try:
            adapter = get_adapter(acc["platform"], acc)
        except Exception as e:
            db.log(acc["platform"], "reply", str(e), ok=0)
            continue
        for part, state in (("public", reply["public_status"]), ("dm", reply["dm_status"])):
            if state == "pending" and not _attempt(reply, part, adapter):
                break
            sent += 1 if state == "pending" else 0
    return sent


def run(now=None):
    poll(now)
    release(now)
