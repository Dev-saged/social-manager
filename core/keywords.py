"""مطابقة كلمات التعليقات (مفاتيح الردود التلقائية) مع فهم للعربية.

مُحوَّل من OpenReply (lib/utils/keyword-matcher.ts)
MIT License, Copyright (c) 2026 Anish Raj, Copyright (c) 2026 Diwen Huang
https://github.com/diwenne/openreply
"""
import re
import unicodedata

_ARABIC_FOLD = [
    (re.compile("[يىے]"), "ی"),
    (re.compile("ك"), "ک"),
    (re.compile("ة"), "ه"),
    (re.compile("[آأإٱ]"), "ا"),
    (re.compile("[ً-ٰٟ]"), ""),   # حركات
    (re.compile("ـ"), ""),                        # تطويل
    (re.compile("[‌‎‏]"), ""),
]
_EMOJI = re.compile(
    "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
    "☀-⛿✀-➿︀-️\U0001F900-\U0001F9FF\U0001FA00-\U0001FAFF‍⃣]"
)
_EASTERN = {c: str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_EASTERN.update({c: str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})


def normalize_arabic(text):
    for pattern, repl in _ARABIC_FOLD:
        text = pattern.sub(repl, text)
    return text.translate(str.maketrans(_EASTERN))


def strip_special(text):
    text = _EMOJI.sub("", text)
    out = [ch if (unicodedata.category(ch)[0] in "LNM" or ch.isspace()) else " " for ch in text]
    return re.sub(r"\s+", " ", "".join(out)).strip()


def fold_diacritics(text):
    """يحذف علامات الحروف اللاتينية فقط ويترك بقية الأبجديات كما هي."""
    out, base_latin = [], False
    for ch in unicodedata.normalize("NFD", text):
        if unicodedata.category(ch).startswith("M"):
            if not base_latin:
                out.append(ch)
            continue
        base_latin = unicodedata.name(ch, "").startswith("LATIN")
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def _clean(text):
    return fold_diacritics(strip_special(normalize_arabic(text))).lower()


_AR_PREFIXES = ("وال", "بال", "كال", "فال", "ال", "لل", "و", "ب", "ل", "ك", "ف")


def _is_arabic(ch):
    return "\u0600" <= ch <= "\u06ff"


def _boundary_before(haystack, i, needle):
    if i == 0 or not haystack[i - 1].isalnum():
        return True
    if _is_arabic(needle[0]):  # "المشروع" تطابق "مشروع"
        for p in _AR_PREFIXES:
            j = i - len(p)
            if j >= 0 and haystack[j:i] == p and (j == 0 or not haystack[j - 1].isalnum()):
                return True
    return False


def _whole_word(haystack, needle):
    start = 0
    while True:
        i = haystack.find(needle, start)
        if i < 0:
            return False
        after = haystack[i + len(needle)] if i + len(needle) < len(haystack) else " "
        if _boundary_before(haystack, i, needle) and not after.isalnum():
            return True
        start = i + 1


def match_keywords(comment, keywords, whole_word=True):
    """يعيد الكلمة المفتاحية المطابقة أو None."""
    text = _clean(comment or "")
    if not text:
        return None
    for keyword in keywords:
        kw = _clean(keyword)
        if not kw:
            continue
        t, k = text, kw
        if re.fullmatch("[0-9oO]+", kw):  # "O8" تُكتب بدل "08"
            t, k = text.replace("o", "0"), kw.replace("o", "0")
        if (_whole_word(t, k) if whole_word else k in t):
            return keyword
    return None
