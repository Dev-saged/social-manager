"""Gemini: توليد منشورات عربية وأخبار تقنية حديثة (مع بحث Google). مفتاحك أنت، اختياري تماماً.

الأساس: Interactions API. إن رفضه الخادم نرجع لـ generateContent القديم الذي ما زال مدعوماً.
"""
import json
import re
import requests
import config
from core import database as db

API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.8-flash"


class GeminiError(RuntimeError):
    pass


def _key():
    key = db.get_gemini_key(config.GEMINI_API_KEY)
    if not key:
        raise GeminiError("لا يوجد مفتاح Gemini. أضفه في الإعدادات.")
    return key


def _model():
    return db.get_setting("gemini_model", "") or DEFAULT_MODEL


def _extract(resp):
    """يستخرج (النص، المصادر) من شكل Interactions أو generateContent."""
    texts, sources = [], []
    for step in resp.get("steps") or []:
        if step.get("type") != "model_output":
            continue
        for part in step.get("content") or []:
            if part.get("type") == "text":
                texts.append(part.get("text", ""))
                for a in part.get("annotations") or []:
                    if a.get("type") == "url_citation" and a.get("url"):
                        sources.append({"title": a.get("title", ""), "url": a["url"]})
    if not texts:
        for cand in resp.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                if part.get("text"):
                    texts.append(part["text"])
            for ch in (cand.get("groundingMetadata") or {}).get("groundingChunks") or []:
                web = ch.get("web") or {}
                if web.get("uri"):
                    sources.append({"title": web.get("title", ""), "url": web["uri"]})
    return "".join(texts).strip(), sources


def _post(url, body):
    try:
        r = requests.post(url, json=body, headers={"x-goog-api-key": _key(), "Content-Type": "application/json"}, timeout=90)
    except requests.RequestException as e:
        raise GeminiError(f"تعذر الاتصال بـ Gemini: {e}") from e
    return r


def call(system, prompt, grounded=False, temperature=0.8):
    model = _model()
    body = {"model": model, "input": prompt, "system_instruction": system,
            "generation_config": {"temperature": temperature}, "store": False}
    if grounded:
        body["tools"] = [{"type": "google_search"}]
    r = _post(f"{API}/interactions", body)
    if r.status_code in (400, 404):
        legacy = {"systemInstruction": {"parts": [{"text": system}]},
                  "contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": temperature}}
        if grounded:
            legacy["tools"] = [{"google_search": {}}]
        r = _post(f"{API}/models/{model}:generateContent", legacy)
    if r.status_code == 429:
        raise GeminiError("تجاوزت حد الاستخدام المجاني، حاول لاحقاً")
    if r.status_code in (401, 403):
        raise GeminiError("مفتاح Gemini غير صالح أو غير مصرّح")
    if r.status_code >= 400:
        raise GeminiError(f"خطأ من Gemini ({r.status_code}): {r.text[:200]}")
    text, sources = _extract(r.json())
    if not text:
        raise GeminiError("رد Gemini فارغ")
    return text, sources


def parse_json(text):
    """يستخرج كائن JSON من رد قد يحتوي سياجات كود أو شرحاً."""
    cleaned = re.sub(r"```(?:json)?", "", text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise GeminiError("تعذر فهم رد Gemini")
    try:
        return json.loads(cleaned[start:end + 1])
    except ValueError as e:
        raise GeminiError("تعذر فهم رد Gemini") from e


def _persona():
    import config as cfg
    return db.get_setting("persona", cfg.DEFAULT_PERSONA)


_FORMAT = ('أعد JSON فقط بهذا الشكل بدون أي شرح: {"title": "عنوان قصير", "text": "نص المنشور الكامل", '
           '"short": "نسخة مختصرة لا تتجاوز 250 حرفاً بنفس المعنى", "hashtags": ["#وسم"]}')


def write_post(topic, tone="راقي وجذاب"):
    system = ("أنت كاتب محتوى عربي لوسائل التواصل. أسلوب صاحب الحساب: " + _persona() +
              " اكتب بالعربية الفصيحة المبسطة بدون مبالغة وبدون رموز تعبيرية كثيرة. " + _FORMAT)
    text, _ = call(system, f"اكتب منشوراً عن: {topic}\nالنبرة: {tone}")
    return _clean(parse_json(text))


def tech_news(recent_titles=(), tone="راقي وجذاب", include_source=True):
    system = ("أنت محرر أخبار تقنية عربي. ابحث في الويب عن أحدث خبر تقني مهم حدث خلال آخر 48 ساعة "
              "(ذكاء اصطناعي، هواتف، برمجة، أمن، شركات تقنية). لا تخترع معلومات، التزم بما وجدته. "
              "اكتب المنشور بأسلوب صاحب الحساب: " + _persona() + " " + _FORMAT)
    avoid = ("\nتجنب هذه العناوين لأنها نُشرت سابقاً: " + "؛ ".join(recent_titles)) if recent_titles else ""
    text, sources = call(system, f"النبرة: {tone}{avoid}", grounded=True, temperature=0.6)
    post = _clean(parse_json(text))
    post["sources"] = sources[:3]
    if include_source and sources:
        link = sources[0]["url"]
        post["text"] = f"{post['text']}\n\nالمصدر: {link}"
        if len(post["short"]) + 25 <= 280:
            post["short"] = f"{post['short']}\n{link}"
    return post


def _clean(d):
    text = str(d.get("text", "")).strip()
    if not text:
        raise GeminiError("لم يُنتج Gemini نصاً")
    tags = [t for t in (d.get("hashtags") or []) if isinstance(t, str)][:6]
    short = str(d.get("short") or "").strip() or text
    return {"title": str(d.get("title", "")).strip()[:120], "text": text, "short": short, "hashtags": tags}
