"""تجديد رموز الدخول عند الحاجة فقط، مع قفل لكل حساب فلا يجدّد عاملان نفس الرمز معاً."""
import threading
import time
from core import database as db
from core import errors

_locks = {}
_guard = threading.Lock()


def _lock(account_id):
    with _guard:
        return _locks.setdefault(account_id, threading.Lock())


def ensure_fresh(adapter, force=False):
    """يجدد الرمز إن اقترب انتهاؤه (أو force بعد خطأ انتهاء). يعيد True إن جُدّد."""
    aid = adapter.account["id"]
    if not force and not adapter.needs_refresh(time.time()):
        return False
    with _lock(aid):
        fresh = db.get_account(aid)
        if fresh is not None and fresh["credentials"] != adapter.account["credentials"] and not force:
            from core import accounts as acc_store
            adapter.creds = acc_store.decrypt(fresh["credentials"])
            if not adapter.needs_refresh(time.time()):
                return False
        try:
            creds = adapter.refresh_token()
        except Exception as e:
            info = errors.classify(e)
            db.log(adapter.name, "token_refresh", errors.text_of(info), ok=0)
            return False
        if not creds:
            return False
        db.update_account_credentials(aid, creds)
        db.log(adapter.name, "token_refresh", adapter.account["username"])
        return True


def refresh_due():
    """من المجدول: يمر على الحسابات التي تدعم التجديد ويجدد المستحق منها."""
    from platforms import get as get_adapter
    done = 0
    for acc in db.list_accounts(active_only=True):
        if acc["platform"] not in ("instagram", "threads"):
            continue
        try:
            adapter = get_adapter(acc["platform"], acc)
        except Exception:
            continue
        if ensure_fresh(adapter):
            done += 1
    return done
