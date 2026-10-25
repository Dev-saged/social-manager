"""تصنيف الأخطاء: كل فشل يتحول إلى رمز ثابت ورسالة عربية وتلميح للحل، ومعرفة هل تفيد إعادة المحاولة."""
import json
import socket

# code: (رسالة، تفيد إعادة المحاولة؟، تلميح)
CODES = {
    "network": ("انقطع الاتصال بالإنترنت أو انتهت المهلة", True, "سيُعاد تلقائياً عند عودة الشبكة"),
    "server": ("خادم المنصة لا يستجيب الآن", True, "مشكلة مؤقتة عند المنصة، تُعاد المحاولة تلقائياً"),
    "rate_limited": ("تجاوزت حد الطلبات المسموح", True, "انتظر قليلاً، تُعاد المحاولة تلقائياً بعد مهلة"),
    "auth_invalid": ("بيانات الدخول أو المفتاح غير صحيح", False, "انسخ المفتاح من جديد من المنصة وألصقه دون مسافات"),
    "token_expired": ("انتهت صلاحية رمز الدخول", False, "أنشئ رمزاً جديداً من لوحة المطوّر ثم حدّث الحساب"),
    "permission": ("الرمز لا يملك الصلاحية المطلوبة", False, "فعّل الصلاحيات المذكورة في دليل المنصة ثم أنشئ رمزاً جديداً"),
    "not_found": ("الحساب أو القناة أو المعرّف غير موجود", False, "تأكد من المعرّف (ID) أو اسم القناة"),
    "bad_request": ("المنصة رفضت الطلب", False, "راجع التفاصيل بالأسفل"),
    "too_large": ("الملف أكبر من المسموح في هذه المنصة", False, "صغّر حجم الملف أو مدته"),
    "media_unsupported": ("صيغة الوسائط غير مقبولة في هذه المنصة", False, "استخدم JPG للصور وMP4 للفيديو"),
    "media_url_needed": ("المنصة تحتاج رابطاً عاماً للوسائط", False, "فعّل مستودع الوسائط (GitHub) في الإعدادات أو أضف كلمة المرور للوضع البديل"),
    "challenge_required": ("Instagram يطلب تأكيد هويتك", False, "افتح «تسجيل الدخول» من بطاقة الحساب وأدخل الرمز الذي يصلك"),
    "two_factor": ("الحساب يستخدم التحقق بخطوتين", False, "افتح «تسجيل الدخول» من بطاقة الحساب وأدخل رمز التطبيق أو الرسالة"),
    "bad_password": ("كلمة المرور غير صحيحة", False, "تأكد من كلمة المرور أو استخدم الدخول بالجلسة"),
    "session_expired": ("انتهت جلسة الدخول المحفوظة", False, "سجّل الدخول من جديد من بطاقة الحساب"),
    "action_blocked": ("Instagram أوقف هذا الإجراء مؤقتاً", False, "توقف عن النشر من هذا الوضع يوماً كاملاً، واستخدم الربط الرسمي"),
    "local_rate": ("وصلت للحد الآمن الذي حددته الأداة لهذه الساعة", True, "سيُكمل وحده بعد انتهاء الساعة"),
    "not_supported": ("هذه العملية غير مدعومة في هذه المنصة", False, ""),
    "lib_missing": ("مكتبة لازمة غير مثبتة على الجهاز", False, "افتح الصحة ← المكتبات واضغط إعادة التثبيت"),
    "invalid_input": ("البيانات المُدخلة ناقصة أو غير صحيحة", False, ""),
    "insufficient_funds": ("نفد الرصيد في حساب المزوّد", False, "اشحن الرصيد أو سيُستخدم المزوّد البديل"),
    "needs_you": ("هذا المنشور ينتظر أن تضغط «نشر» بنفسك", False, "افتح المنشور واضغط «شارك الآن»"),
    "unknown": ("حدث خطأ غير متوقع", False, "راجع التفاصيل بالأسفل"),
}


class AppError(RuntimeError):
    """خطأ مصنَّف مسبقاً من داخل الأداة."""

    def __init__(self, code, detail=""):
        self.code = code if code in CODES else "unknown"
        self.detail = detail
        super().__init__(detail or CODES[self.code][0])


class ManualShare(AppError):
    """المنصة لا تسمح بالنشر الآلي لهذا الحساب: يُجهَّز المنشور وينتظر ضغطك."""

    def __init__(self, detail=""):
        super().__init__("needs_you", detail)


_IG_NAMES = {
    "ChallengeRequired": "challenge_required", "ChallengeUnknownStep": "challenge_required",
    "SelectContactPointRecoveryForm": "challenge_required", "RecaptchaChallengeForm": "challenge_required",
    "TwoFactorRequired": "two_factor", "BadPassword": "bad_password", "BadCredentials": "bad_password",
    "LoginRequired": "session_expired", "ReloginAttemptExceeded": "session_expired",
    "PleaseWaitFewMinutes": "rate_limited", "RateLimitError": "rate_limited", "ClientThrottledError": "rate_limited",
    "FeedbackRequired": "action_blocked", "SentryBlock": "action_blocked",
    "RateLimitExceeded": "local_rate", "NotSupported": "not_supported",
    "TooManyRequests": "rate_limited", "Unauthorized": "auth_invalid", "Forbidden": "permission", "NotFound": "not_found",
}


def _chain(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _body(resp):
    try:
        return resp.json()
    except Exception:
        try:
            return json.loads(resp.text or "{}")
        except Exception:
            return {"raw": (getattr(resp, "text", "") or "")[:300]}


def _from_body(status, body):
    """يقرأ صيغ الأخطاء الشائعة (Meta, Telegram, OpenAI-compatible, Google) ويعيد (code, detail)."""
    err = body.get("error") if isinstance(body, dict) else None
    detail = ""
    if isinstance(err, dict):
        detail = str(err.get("message") or err.get("error_user_msg") or "")
        meta = err.get("code")
        sub = err.get("error_subcode")
        etype = str(err.get("type") or err.get("status") or "")
        if meta == 190 or sub in (463, 467) or "expired" in detail.lower():
            return "token_expired", detail
        if meta in (10, 200, 3) or str(meta).startswith("2") and len(str(meta)) == 3 or "insufficient_scope" in json.dumps(err):
            return "permission", detail
        if meta in (4, 17, 32, 613) or etype == "rate_limit_error" or etype == "RESOURCE_EXHAUSTED":
            return "rate_limited", detail
        if etype == "insufficient_funds" or status == 402:
            return "insufficient_funds", detail
        if meta == 9004 or sub == 2207052 or "media" in detail.lower() and "format" in detail.lower():
            return "media_unsupported", detail
    elif isinstance(body, dict) and "description" in body:
        detail = str(body.get("description"))
        if "chat not found" in detail.lower():
            return "not_found", detail
    elif isinstance(body, dict) and body.get("message"):
        detail = str(body.get("message"))
    elif isinstance(err, str):
        detail = err
    if not detail and isinstance(body, dict):
        detail = str(body.get("raw") or body.get("detail") or "")[:300]
    return None, detail


def _from_status(status):
    if status == 401:
        return "auth_invalid"
    if status == 402:
        return "insufficient_funds"
    if status == 403:
        return "permission"
    if status == 404:
        return "not_found"
    if status == 413:
        return "too_large"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server"
    if status >= 400:
        return "bad_request"
    return None


def classify(exc):
    """يعيد {code, message, hint, retryable, detail} لأي استثناء."""
    try:
        import requests
        net_types = (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                     requests.exceptions.ChunkedEncodingError)
    except ImportError:
        net_types = ()
    code, detail = None, ""
    for e in _chain(exc):
        if isinstance(e, AppError):
            code, detail = e.code, e.detail
            break
        name = type(e).__name__
        if name in _IG_NAMES:
            code = _IG_NAMES[name]
            detail = str(e)[:300]
            break
        if isinstance(e, (ConnectionError, TimeoutError, socket.gaierror, socket.timeout) + net_types):
            code, detail = "network", str(e)[:200]
            break
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        if isinstance(status, int):
            body_code, detail = _from_body(status, _body(resp))
            code = body_code or _from_status(status)
            break
        if isinstance(e, ImportError) or "غير مثبتة" in str(e):
            code, detail = "lib_missing", str(e)
            break
        if isinstance(e, (ValueError, KeyError)):
            code, detail = "invalid_input", str(e)
            break
    if code is None:
        code, detail = "unknown", str(exc)[:300]
    msg, retry, hint = CODES[code]
    return {"code": code, "message": msg, "hint": hint, "retryable": retry, "detail": detail if detail != msg else ""}


def text_of(info):
    """سطر واحد يُخزَّن في قاعدة البيانات ويُعرض في الواجهة."""
    return info["message"] + (f": {info['detail']}" if info.get("detail") else "")
