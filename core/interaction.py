"""محرك التفاعل: مراقبة منشورات الأهداف، تحليلها، واقتراح تعليق لكل بوست.

سير العمل مصمم ليبقى تحت سيطرتك: الأداة تحلل وتقترح، وأنت توافق أو ترفض من الواجهة.
لا يُنشر أي تعليق دون موافقتك اليدوية. هذا هو الخط الفاصل بين مساعد ذكي وحساب سبام.
"""
import json
from core import database as db
from core import ai_engine
from platforms import get as get_adapter


def build_queue(limit_per_target=3, on_progress=None):
    """يمرّ على كل الأهداف المضافة، يجلب أحدث منشوراتهم، يحللها ويقترح تعليقات.

    on_progress: دالة اختيارية تستقبل نص تقدم لعرضه في الواجهة.
    تعيد عدد الاقتراحات الجديدة.
    """
    persona = db.get_setting("persona", None)
    if not persona:
        import config
        persona = config.DEFAULT_PERSONA
    added = 0
    for target in db.list_targets():
        acc = db.get_account(target["account_id"])
        if not acc or not acc["active"]:
            continue
        if on_progress:
            on_progress(f"جلب منشورات {target['username']} عبر {acc['platform']}...")
        try:
            adapter = get_adapter(acc["platform"], acc)
            posts = adapter.recent_posts(target["username"], limit=limit_per_target)
        except Exception as e:
            db.log(acc["platform"], "fetch", f"{target['username']}: {e}", ok=0)
            continue
        for p in posts:
            row_id = db.add_to_queue(
                acc["id"], acc["platform"], target["username"],
                p["id"], p["text"], {}, ""
            )
            if row_id is None:
                continue  # سبق أن صُفّ هذا البوست
            if on_progress:
                on_progress(f"تحليل بوست {target['username']}...")
            try:
                analysis = ai_engine.analyze_post(p["text"], p.get("image_b64"))
                comment = ai_engine.generate_comment(analysis, persona, p["text"])
            except Exception as e:
                db.log(acc["platform"], "ai", str(e), ok=0)
                continue
            # حدّث الصف بالتحليل والتعليق المقترح
            with db._conn() as c:
                c.execute(
                    "UPDATE comment_queue SET analysis=?, proposed_comment=? WHERE id=?",
                    (json.dumps(analysis, ensure_ascii=False), comment, row_id),
                )
            added += 1
        if added >= 10:  # دفعة معقولة في كل تشغيل
            break
    db.log("system", "build_queue", f"اقتراحات جديدة: {added}")
    return added


def approve(comment_id):
    db.approve_comment(comment_id)  # ينشرها المجدول تلقائياً بعد تأخير عشوائي


def reject(comment_id):
    db.reject_comment(comment_id)


def edit_and_approve(comment_id, new_text):
    with db._conn() as c:
        c.execute("UPDATE comment_queue SET proposed_comment=? WHERE id=?", (new_text, comment_id))
    db.approve_comment(comment_id)
