"""Oxion AI: واجهة متوافقة مع OpenAI (https://oxionai.com/v1). المزوّد الأساسي للنصوص."""
import json
import time
import requests
from core import database as db
from core.errors import AppError

API = "https://oxionai.com/v1"
# ترتيب التفضيل (يُستخدم الأول المتاح في قائمتك الحية، وإلا الأرخص الذي يملك القدرات المطلوبة)
PREFER_TEXT = ["gpt-5.6-luna", "deepseek-v4-flash-0731", "qwen3.8-27b", "gemini-3.7-flash"]
PREFER_WEB = ["gemini-3.7-flash", "kimi-k2.6", "glm-5.3", "qwen3.8-max"]
PREFER_VISION = ["gpt-5.6-luna", "seed-2.1-turbo", "gemini-3.7-flash"]
CACHE_TTL = 6 * 3600


def key():
    import config
    return db.get_api_key(config.OXION_API_KEY)


def _headers():
    k = key()
    if not k:
        raise AppError("auth_invalid", "لا يوجد مفتاح Oxion. أضفه في الإعدادات")
    return {"Authorization": "Bearer " + k, "Content-Type": "application/json"}


def models(force=False):
    """قائمة النماذج الحية مع القدرات والتسعير، مخزنة 6 ساعات."""
    cached = db.get_setting("oxion_models", "")
    if cached and not force:
        try:
            data = json.loads(cached)
            if time.time() - data.get("at", 0) < CACHE_TTL:
                return data["list"]
        except ValueError:
            pass
    r = requests.get(f"{API}/models", headers=_headers(), timeout=20)
    r.raise_for_status()
    out = []
    for m in r.json().get("data", []):
        price = m.get("pricing") or {}
        out.append({"id": m.get("id", ""), "name": m.get("name") or m.get("id", ""), "type": m.get("type", "chat"),
                    "caps": m.get("capabilities") or [], "context": m.get("context_window") or 0,
                    "in": float(price.get("input_per_1k") or 0), "out": float(price.get("output_per_1k") or 0)})
    db.set_setting("oxion_models", json.dumps({"at": time.time(), "list": out}, ensure_ascii=False))
    return out


def recommend(lst, need=()):
    """يختار نموذجاً: الأول من قائمة التفضيل المتاحة، وإلا الأرخص الذي يملك كل القدرات المطلوبة."""
    need = set(need)
    pref = PREFER_WEB if "web_search" in need else PREFER_VISION if "vision" in need else PREFER_TEXT
    ok = [m for m in lst if m["type"] in ("chat", "") and need <= set(m["caps"])]
    ids = {m["id"] for m in ok}
    for p in pref:
        if p in ids:
            return p
    ok.sort(key=lambda m: m["in"] + m["out"])
    return ok[0]["id"] if ok else ""


def pick(role):
    """role: text / web / vision. يحترم اختيارك في الإعدادات وإلا يرشّح تلقائياً."""
    chosen = db.get_setting(f"oxion_model_{role}", "").strip()
    if chosen:
        return chosen
    need = {"text": (), "web": ("web_search",), "vision": ("vision",)}[role]
    try:
        return recommend(models(), need)
    except Exception:
        return {"text": PREFER_TEXT[0], "web": PREFER_WEB[0], "vision": PREFER_VISION[0]}[role]


def chat(messages, role="text", json_mode=False, temperature=0.8, max_tokens=None):
    """يعيد (النص، المصادر، النموذج)."""
    model = pick(role)
    body = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens:
        body["max_tokens"] = max(max_tokens, 2048)  # نماذج التفكير تستهلك جزءاً من الميزانية
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(f"{API}/chat/completions", headers=_headers(), json=body, timeout=120)
    r.raise_for_status()
    choice = (r.json().get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = (msg.get("content") or "").strip()
    if not text:
        raise AppError("server", "رد Oxion فارغ")
    sources = []
    for a in msg.get("annotations") or []:
        u = a.get("url_citation") or a
        if u.get("url"):
            sources.append({"title": u.get("title", ""), "url": u["url"]})
    return text, sources, model
