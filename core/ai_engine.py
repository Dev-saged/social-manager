"""محرك الذكاء: تحليل البوستات وتوليد التعليقات والمحتوى عبر سلسلة المزوّدين (Oxion ثم Gemini)."""
import json
from core import llm


def _chat(system, user, json_mode=False, images=None, temperature=0.8):
    return llm.complete(system, user, json_mode=json_mode, images=images, temperature=temperature)["text"]


def analyze_post(post_text, image_b64=None):
    """يحلل البوست ويعيد JSON: الموضوع، النبرة، المشاعر، وزاوية التعليق المناسبة."""
    system = (
        "أنت محلل محتوى عربي خبير. حلل منشورات وسائل التواصل وأعد JSON فقط "
        'بهذا الشكل: {"topic": "...", "tone": "...", "sentiment": "positive/neutral/negative", '
        '"comment_angle": "زاوية تعليق ذكية تناسب البوست"}'
    )
    raw = _chat(system, post_text or "(منشور بدون نص)", json_mode=True, images=[image_b64] if image_b64 else None)
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
    return _chat(system, user).strip()


def generate_content(topic, platform, tone="راقي وجذاب"):
    """يولّد منشوراً كاملاً: نص + هاشتاغات + نصيحة توقيت. يعيد JSON."""
    system = (
        "أنت خبير محتوى عربي لوسائل التواصل. أعد JSON فقط بهذا الشكل: "
        '{"caption": "نص المنشور", "hashtags": ["#..."], "timing_tip": "أفضل وقت للنشر"}\n'
        f"المنصة: {platform}. اجعل النص مناسباً للمنصة: تويتر قصير، انستغرام وفيسبوك أطول مع سطور سهلة، يوتيوب عنوان ووصف."
    )
    raw = _chat(system, f"اكتب منشوراً عن: {topic}. النبرة: {tone}", json_mode=True)
    try:
        return json.loads(raw)
    except Exception:
        return {"caption": raw, "hashtags": [], "timing_tip": ""}
