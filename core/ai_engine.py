"""محرك الذكاء: تحليل البوستات وتوليد التعليقات والمحتوى عبر Thaura API."""
import base64
import requests
import config
from core import database as db

_client = None  # المفتاح المخزَّن مؤقتاً، يُصفَّر من الإعدادات عند تغييره


def _key():
    global _client
    if _client is None:
        api_key = config.THAURA_API_KEY or db.get_api_key("")
        if not api_key:
            raise RuntimeError("لا يوجد مفتاح Thaura API. أضفه في الإعدادات.")
        _client = api_key
    return _client


def _chat(messages, json_mode=False, max_tokens=800):
    body = {"model": config.THAURA_MODEL, "messages": messages, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(config.THAURA_BASE_URL + "/chat/completions", json=body, timeout=60,
                      headers={"Authorization": "Bearer " + _key()})
    if not r.ok:
        raise RuntimeError(f"Thaura {r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


def analyze_post(post_text, image_b64=None):
    """يحلل البوست ويعيد JSON: الموضوع، النبرة، المشاعر، وزاوية التعليق المناسبة."""
    system = (
        "أنت محلل محتوى عربي خبير. حلل منشورات وسائل التواصل وأعد JSON فقط "
        'بهذا الشكل: {"topic": "...", "tone": "...", "sentiment": "positive/neutral/negative", '
        '"comment_angle": "زاوية تعليق ذكية تناسب البوست"}'
    )
    content = [{"type": "text", "text": post_text or "(منشور بدون نص)"}]
    if image_b64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}})
    raw = _chat([{"role": "system", "content": system}, {"role": "user", "content": content}], json_mode=True)
    import json
    try:
        return json.loads(raw)
    except Exception:
        return {"topic": raw[:100], "tone": "غير محدد", "sentiment": "neutral", "comment_angle": ""}


def generate_comment(analysis, persona, post_text):
    """يولّد تعليقاً واحداً يليق بالبووست حسب الشخصية المضبوطة."""
    system = (
        f"أنت تكتب تعليقات على منشورات بصوت صاحب الحساب. أسلوبك: {persona}\n"
        "قواعد: تعليق واحد فقط، من 5 إلى 20 كلمة، بدون رموز تعبيرية كثيرة، "
        "بدون حشو أو مجاملات عامة مثل (تصوير رائع جداً)، ويجب أن يعكس فهماً حقيقياً لفكرة البوست."
    )
    user = f"تحليل البوست: {analysis}\n\nنص البوست: {post_text}\n\nاكتب التعليق الآن (النص فقط بدون أي شرح):"
    return _chat([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=200).strip()


def generate_content(topic, platform, tone="راقي وجذاب"):
    """يولّد منشوراً كاملاً: نص + هاشتاغات + نصيحة توقيت. يعيد JSON."""
    system = (
        "أنت خبير محتوى عربي لوسائل التواصل. أعد JSON فقط بهذا الشكل: "
        '{"caption": "نص المنشور", "hashtags": ["#..."], "timing_tip": "أفضل وقت للنشر"}\n'
        f"المنصة: {platform}. اجعل النص مناسباً للمنصة: تويتر قصير، انستغرام وفيسبوك أطول مع سطور سهلة، يوتيوب عنوان ووصف."
    )
    import json
    raw = _chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": f"اكتب منشوراً عن: {topic}. النبرة: {tone}"}],
        json_mode=True,
    )
    try:
        return json.loads(raw)
    except Exception:
        return {"caption": raw, "hashtags": [], "timing_tip": ""}
