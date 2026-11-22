"""محرك نشر المجموعات: كل منصة هدف مستقل، يُحجز قبل الإرسال، ويُؤجل فقط عند رفض مؤكد بسبب حد المعدل."""
import time
from core import database as db
from core import media
from core import net
from core import errors
from core import queues
from core import tokens
from platforms import get as get_adapter

BACKOFF = [60, 300, 900, 3600, 10800]  # ثوانٍ بين المحاولات
_last_sweep = {"at": 0.0}


def grace_seconds():
    try:
        return float(db.get_setting("missed_grace_hours", "12")) * 3600
    except ValueError:
        return 12 * 3600


def run_due(now=None):
    """يمر على الأهداف المستحقة. لا يفعل شيئاً أوفلاين فلا تُستهلك المحاولات.

    كل منصة في عامل مستقل، والمنصة المشغولة تُترك لدورتها فلا تعطل غيرها."""
    now = time.time() if now is None else now
    db.recover_stuck_targets(now, busy_platforms=queues.busy_platforms())
    if not net.online():
        return 0
    done = 0
    for plat in db.due_platforms(now):
        if queues.is_busy(plat):
            continue
        batch = db.claim_due_targets(now, grace_seconds(), platform=plat, limit=10)
        if not batch:
            continue
        done += len(batch)
        queues.submit(plat, lambda b=batch: [_publish_one(t) for t in b])
    queues.wait_all(timeout=20)
    _sweep_media(now)
    return done


def _publish_one(target):
    group = db.get_group(target["group_id"])
    adapter = None
    sending = False
    try:
        acc = db.get_account(target["account_id"])
        if not acc or not acc["active"]:
            db.finish_target(target["id"], "failed", "الحساب غير موجود أو غير مفعّل", error_code="not_found")
            return
        text = target["text_override"] or group["text"]
        path = media.path_for(group["media_id"]) if group["media_id"] else None
        adapter = get_adapter(acc["platform"], acc)
        if hasattr(adapter, "needs_refresh"):
            tokens.ensure_fresh(adapter)
        sending = True
        remote = adapter.publish_media(text, path, group["media_kind"])
        if not remote:
            raise errors.DeliveryUncertain("لم يصل معرّف المنشور من المنصة؛ افحص الحساب قبل إعادة المحاولة")
        db.finish_target(target["id"], "published", remote_id=str(remote))
        db.record_delay(target["id"], acc["platform"], target["due_at"])
        db.log(acc["platform"], "publish", f"group #{group['id']}")
    except errors.ManualShare as e:
        db.finish_target(target["id"], "needs_you", str(e), error_code="needs_you")
        db.log(target["platform"], "publish", "بانتظار مشاركتك اليدوية")
    except Exception as e:
        info = errors.classify(e)
        if sending and (info["code"] in ("network", "server", "unknown") or isinstance(e, (KeyError, ValueError)) and not isinstance(e, errors.AppError)):
            info = errors.classify(errors.AppError("outcome_unknown"))
        _on_error(target, e, info)
    _release_media(target["group_id"])


def _on_error(target, exc, info=None):
    info = info or errors.classify(exc)
    transient = info["retryable"] and info["code"] in ("rate_limited", "local_rate")
    tried = target["attempts"]  # قبل هذه المحاولة (الحجز زاد العدّاد في القاعدة فقط)
    msg = errors.text_of(info)
    if transient and tried < len(BACKOFF):
        wait = BACKOFF[tried]
        db.finish_target(target["id"], "waiting_network", msg, next_try_at=time.time() + wait, error_code=info["code"])
    else:
        state = "outcome_unknown" if info["code"] == "outcome_unknown" else "failed"
        db.finish_target(target["id"], state, msg, error_code=info["code"])
        if info["code"] in ("auth_invalid", "token_expired", "permission", "challenge_required", "two_factor",
                            "bad_password", "session_expired", "action_blocked"):
            db.set_health(target["account_id"], dict(info, ok=False))
    db.log(target["platform"], "publish", msg, ok=0)


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
