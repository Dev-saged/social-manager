"""سلسلة مزوّدي الذكاء: Oxion أولاً، وعند أي فشل يتحول إلى Gemini تلقائياً."""
from core import database as db
from core import errors


def chain():
    order = [p.strip() for p in db.get_setting("ai_order", "oxion,gemini").split(",") if p.strip()]
    return [p for p in order if p in ("oxion", "gemini")] or ["oxion", "gemini"]


def _available(name):
    if name == "oxion":
        from core import oxion
        return bool(oxion.key())
    import config
    return bool(db.get_gemini_key(config.GEMINI_API_KEY))


def complete(system, prompt, web=False, json_mode=False, temperature=0.8, images=None):
    """يعيد {text, sources, provider, model, fallback_from}. images: [base64 jpeg]."""
    from core import gemini
    tried = []
    last = None
    for name in chain():
        if not _available(name):
            continue
        try:
            if name == "oxion":
                from core import oxion
                content = prompt
                if images:
                    content = [{"type": "text", "text": prompt}] + [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in images]
                role = "web" if web else "vision" if images else "text"
                text, sources, model = oxion.chat([{"role": "system", "content": system}, {"role": "user", "content": content}],
                                                  role=role, json_mode=json_mode, temperature=temperature)
            else:
                text, sources = gemini.call(system, prompt, grounded=web, temperature=temperature)
                model = gemini._model()
            return {"text": text, "sources": sources, "provider": name, "model": model, "fallback_from": tried[:]}
        except Exception as e:
            info = errors.classify(e)
            last = e
            tried.append(name)
            db.log("ai", name, errors.text_of(info), ok=0)
    if last is None:
        raise gemini.GeminiError("لا يوجد مفتاح ذكاء. أضف مفتاح Oxion أو Gemini في الإعدادات")
    if isinstance(last, gemini.GeminiError):
        raise last
    raise gemini.GeminiError(errors.text_of(errors.classify(last)))
