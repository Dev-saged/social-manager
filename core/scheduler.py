"""المجدول: قلب التشغيل التلقائي.

كل دقيقة يفحص: منشورات وصل وقت نشرها، وتعليقات موافق عليها انتهى تأخيرها الواقعي.
التأخير العشوائي (config.RATE_LIMITS) يجعل الإيقاع بشرياً وليس آلياً ثابتاً.
"""
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from core import database as db
from platforms import get as get_adapter
from platforms.base import RateLimitExceeded

_sched = None


def start():
    global _sched
    if _sched is not None:
        return _sched
    db.init_db()
    _sched = BackgroundScheduler(daemon=True)
    _sched.add_job(tick, "interval", seconds=60, id="tick", max_instances=1, coalesce=True)
    _sched.start()
    return _sched


def stop():
    global _sched
    if _sched:
        _sched.shutdown(wait=False)
        _sched = None


def tick():
    try:
        _publish_due()
    except Exception as e:
        db.log("system", "publish_tick", str(e), ok=0)
    try:
        _release_approved_comments()
    except Exception as e:
        db.log("system", "comment_tick", str(e), ok=0)


def _adapter_for(account_id):
    acc = db.get_account(account_id)
    if not acc or not acc["active"]:
        return None, None
    return get_adapter(acc["platform"], acc), acc


def _publish_due():
    for post in db.due_posts():
        acc = db.get_account(post["account_id"])
        try:
            adapter, acc = _adapter_for(post["account_id"])
            if not adapter:
                db.mark_post_status(post["id"], "failed", "الحساب غير موجود أو غير مفعّل")
                continue
            adapter.publish(post["content"], post["media_path"])
            db.mark_post_status(post["id"], "published")
            db.log(acc["platform"], "publish", f"post #{post['id']}")
        except Exception as e:
            db.mark_post_status(post["id"], "failed", str(e))
            db.log(acc["platform"] if acc else "system", "publish", str(e), ok=0)


def _release_approved_comments():
    for c in db.approved_due():
        acc = db.get_account(c["account_id"])
        try:
            adapter, acc = _adapter_for(c["account_id"])
            if not adapter:
                db.mark_comment_status(c["id"], "failed", "الحساب غير مفعّل")
                continue
            adapter.comment(c["post_id"], c["proposed_comment"])
            db.mark_comment_status(c["id"], "posted")
            db.log(c["platform"], "comment", f"{c['target_username']} :: {c['proposed_comment'][:60]}")
        except RateLimitExceeded as e:
            db.defer_rate_limited_comment(c["id"])
            db.log(c["platform"], "comment_deferred", str(e), ok=0)
        except Exception as e:
            db.mark_comment_status(c["id"], "failed", str(e))
            db.log(c["platform"], "comment", str(e), ok=0)
