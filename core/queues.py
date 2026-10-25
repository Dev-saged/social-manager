"""عامل مستقل لكل منصة: بطء رفع فيديو على Instagram لا يؤخر منشور Telegram."""
import threading

_busy = {}
_lock = threading.Lock()


def busy_platforms():
    with _lock:
        return [p for p, t in _busy.items() if t.is_alive()]


def is_busy(platform):
    with _lock:
        t = _busy.get(platform)
        return bool(t and t.is_alive())


def submit(platform, fn):
    """يشغّل fn في خيط المنصة. يعيد False إن كان عاملها مشغولاً."""
    with _lock:
        t = _busy.get(platform)
        if t and t.is_alive():
            return False
        t = threading.Thread(target=fn, name=f"pub-{platform}", daemon=True)
        _busy[platform] = t
        t.start()
        return True


def wait_all(timeout=None):
    with _lock:
        threads = list(_busy.values())
    for t in threads:
        t.join(timeout)
