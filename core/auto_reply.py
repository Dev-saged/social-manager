"""الردود التلقائية: تعليق فيه كلمتك المفتاحية يستقبل رداً علنياً ورسالة خاصة.

لا Webhook هنا (التطبيق على جوالك): نفحص التعليقات كل دقيقتين. كل رد يُحجز قبل إرساله
فلا يتكرر أبداً، وما كانت نتيجته غير مؤكدة يُعلَّم failed ويمكنك إعادته يدوياً.
"""
import json
import random
import time
import config
from core import database as db
from core import keywords
from core import net
from platforms import get as get_adapter
from platforms.base import RateLimitExceeded

_last_poll = {}


def match_rule(rules, comment):
    """أول قاعدة تنطبق (القواعد الخاصة بمنشور تسبق العامة). يعيد القاعدة أو None."""
    for rule in rules:
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
            comments = get_adapter(acc["platform"], acc).recent_comments()
        except Exception as e:
            db.log(acc["platform"], "reply_poll", str(e), ok=0)
            continue
        for c in sorted(comments, key=lambda x: x["ts"]):
            if db.seen_comment(account_id, c["comment_id"]):
                continue
            rule = match_rule(rules, c)
            if not rule or c["ts"] < rule["created_at"]:
                continue
            public, dm = compose(rule, c)
            if not (public or dm):
                continue
            release = now + random.uniform(*config.REPLY_DELAY_RANGE)
            if db.queue_reply(rule["id"], account_id, c, public, dm, release):
                added += 1
    return added


def _attempt(reply, part, adapter):
    """يرسل جزءاً واحداً. 'sending' يُسجَّل قبل الإرسال حتى لا يتكرر. يعيد False إن أُجّل."""
    limiter = adapter.reply_limiter if part == "public" else adapter.dm_limiter
    if not limiter.allow():
        db.set_reply_part(reply["id"], part, "pending", release_at=time.time() + 300)
        return False
    db.set_reply_part(reply["id"], part, "sending")
    try:
        if part == "public":
            adapter.reply_to_comment(reply["media_id"], reply["comment_id"], reply["public_text"])
        else:
            adapter.send_dm(reply["user_id"], reply["dm_text"], reply["comment_id"])
        db.set_reply_part(reply["id"], part, "sent")
        db.log(adapter.name, "reply_" + part, f"@{reply['username']}")
    except RateLimitExceeded as e:
        db.set_reply_part(reply["id"], part, "pending", str(e), release_at=time.time() + 300)
        return False
    except Exception as e:
        db.set_reply_part(reply["id"], part, "failed", str(e))
        db.log(adapter.name, "reply_" + part, str(e), ok=0)
    return True


def release(now=None):
    now = time.time() if now is None else now
    db.recover_stuck_replies(now)
    if not net.online():
        return 0
    sent = 0
    for reply in db.due_replies(now):
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
