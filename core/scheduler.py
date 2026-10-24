"""المجدول: قلب التشغيل التلقائي.

كل دقيقة يفحص: منشورات وصل وقت نشرها، وتعليقات موافق عليها انتهى تأخيرها الواقعي.
التأخير العشوائي (config.RATE_LIMITS) يجعل الإيقاع بشرياً وليس آلياً ثابتاً.
"""
import datetime
import os
import threading
import time
from apscheduler.schedulers.background import BackgroundScheduler
from core import database as db
from core import publisher
from core import auto_reply
from core import auto_rules
from core import media
from platforms import get as get_adapter
from platforms.base import RateLimitExceeded

_sched = None
_last_refresh = {"at": 0.0}
_tick_lock = threading.Lock()


def start():
    global _sched
    if _sched is not None:
        return _sched
    db.init_db()
    # UTC صريح: أندرويد بلا قاعدة مناطق زمنية، والمهام فترية لا تحتاج منطقة محلية
    _sched = BackgroundScheduler(daemon=True, timezone=datetime.timezone.utc)
    _sched.add_job(tick, "interval", seconds=30, id="tick", max_instances=1, coalesce=True)
    _sched.start()
    db.set_setting("sched_started_at", str(time.time()))
    return _sched


def running():
    return _sched is not None and _sched.running


def stop():
    global _sched
    if _sched:
        _sched.shutdown(wait=False)
        _sched = None


def tick():
    """دورة واحدة. القفل يمنع تداخل دورة المجدول مع استدراك يطلبه فتح التطبيق."""
    if not _tick_lock.acquire(blocking=False):
        return False
    try:
        _tick()
    finally:
        _tick_lock.release()
    return True


def _tick():
    db.set_setting("hb_at", str(time.time()))
    if time.time() - _last_refresh["at"] > 3600:
        _last_refresh["at"] = time.time()
        try:
            from core import tokens
            tokens.refresh_due()
        except Exception as e:
            db.log("system", "token_tick", str(e), ok=0)
    try:
        _publish_due()
    except Exception as e:
        db.log("system", "publish_tick", str(e), ok=0)
    try:
        publisher.run_due()
    except Exception as e:
        db.log("system", "groups_tick", str(e), ok=0)
    try:
        auto_rules.run_due()
    except Exception as e:
        db.log("system", "rules_tick", str(e), ok=0)
    try:
        auto_reply.run()
    except Exception as e:
        db.log("system", "reply_tick", str(e), ok=0)
    try:
        _release_approved_comments()
    except Exception as e:
        db.log("system", "comment_tick", str(e), ok=0)


def _adapter_for(account_id):
    acc = db.get_account(account_id)
    if not acc or not acc["active"]:
        return None, None
    return get_adapter(acc["platform"], acc), acc


def _drop_local_media(media_path):
    """ينظف ملف الوسائط المحلي بعد نشره (الروابط العامة تُتجاهل)."""
    if media_path and not str(media_path).startswith("http"):
        name = os.path.basename(str(media_path))
        if media.valid_id(name) and os.path.dirname(str(media_path)) == media.media_dir():
            media.delete(name)


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
            _drop_local_media(post["media_path"])
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
