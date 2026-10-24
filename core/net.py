"""فحص الاتصال بالإنترنت (خفيف) وتصنيف أخطاء الشبكة المؤقتة."""
import socket
import time

PROBES = [("1.1.1.1", 443), ("8.8.8.8", 53)]
_cache = {"at": 0.0, "ok": True}


def online(max_age=20):
    now = time.time()
    if now - _cache["at"] < max_age:
        return _cache["ok"]
    ok = False
    for host, port in PROBES:
        try:
            with socket.create_connection((host, port), timeout=3):
                ok = True
                break
        except OSError:
            continue
    _cache.update(at=now, ok=ok)
    return ok


def is_transient(exc):
    """هل الخطأ مؤقت (شبكة، 429، 5xx) فيُعاد المحاولة، أم دائم (دخول، صيغة)؟"""
    import requests
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (ConnectionError, TimeoutError, socket.gaierror, socket.timeout,
                            requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                            requests.exceptions.ChunkedEncodingError)):
            return True
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
        if isinstance(code, int) and (code == 429 or code >= 500):
            return True
        exc = exc.__cause__ or exc.__context__
    return False
