"""محرك نشر المجموعات: كل منصة هدف مستقل، يُحجز قبل الإرسال، ويُعاد عند انقطاع الشبكة فقط."""
import time
from core import database as db
from core import media
from core import net
from platforms import get as get_adapter

BACKOFF = [60, 300, 900, 3600, 10800]  # ثوانٍ بين المحاولات
_last_sweep = {"at": 0.0}


def grace_seconds():
    try:
        return float(db.get_setting("missed_grace_hours", "12")) * 3600
    except ValueError:
        return 12 * 3600


def run_due(now=None):
    """يمر على الأهداف المستحقة. لا يفعل شيئاً أوفلاين فلا تُستهلك المحاولات."""
    now = time.time() if now is None else now
    db.recover_stuck_targets(now)
    if not net.online():
        return 0
    done = 0
    for target in db.claim_due_targets(now, grace_seconds()):
        _publish_one(target)
        done += 1
    _sweep_media(now)
    return done


def _publish_one(target):
    group = db.get_group(target["group_id"])
    try:
        acc = db.get_account(target["account_id"])
        if not acc or not acc["active"]:
            db.finish_target(target["id"], "failed", "الحساب غير موجود أو غير مفعّل")
            return
        text = target["text_override"] or group["text"]
        path = media.path_for(group["media_id"]) if group["media_id"] else None
        adapter = get_adapter(acc["platform"], acc)
        remote = adapter.publish_media(text, path, group["media_kind"])
        db.finish_target(target["id"], "published", remote_id=str(remote or ""))
        db.log(acc["platform"], "publish", f"group #{group['id']}")
    except Exception as e:
        _on_error(target, e)
    _release_media(target["group_id"])


def _on_error(target, exc):
    transient = net.is_transient(exc)
    tried = target["attempts"]  # قبل هذه المحاولة (الحجز زاد العدّاد في القاعدة فقط)
    if transient and tried < len(BACKOFF):
        wait = BACKOFF[tried]
        db.finish_target(target["id"], "waiting_network", str(exc), next_try_at=time.time() + wait)
    else:
        db.finish_target(target["id"], "failed", str(exc))
    db.log(target["platform"], "publish", str(exc), ok=0)


def _release_media(group_id):
    media_id = db.group_media_if_done(group_id)
    if media_id:
        media.delete(media_id)
        db.clear_group_media(group_id)


def _sweep_media(now):
    if now - _last_sweep["at"] < 3600:
        return
    _last_sweep["at"] = now
    media.sweep(db.referenced_media_ids())
