"""قواعد التوليد المتكررة: في موعد كل قاعدة يكتب Gemini منشوراً ويضعه مسودة للموافقة (أو يجدوله مباشرة)."""
import datetime
import json
import time
import config
from core import database as db
from core import gemini
from core import net
from core.routes_v2 import validate_targets

WINDOW = 6 * 3600       # بعد الموعد بهذه المدة نتخطى الخانة
RETRY_EVERY = 300       # لا نعيد استدعاء Gemini لنفس القاعدة قبل 5 دقائق عند الفشل
_last_try = {}


def _slots_due(rule, now_dt):
    due = []
    for hhmm in json.loads(rule["times"] or "[]"):
        try:
            hh, mm = map(int, hhmm.split(":"))
        except ValueError:
            continue
        slot_dt = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        key = slot_dt.strftime("%Y-%m-%d %H:%M")
        age = (now_dt - slot_dt).total_seconds()
        if 0 <= age <= WINDOW and key > (rule["last_slot"] or ""):
            due.append(key)
    return sorted(due)


def _build_targets(post, account_ids):
    """لكل حساب: النص الأصلي أو المختصر إن طال، ويُستبعد الحساب الذي لا يصلح له النص."""
    from platforms import capabilities
    caps = capabilities()
    keep = []
    for aid in account_ids:
        acc = db.get_account(aid)
        if not acc:
            continue
        limit = caps.get(acc["platform"], {}).get("max_len", 0)
        override = post["short"] if len(post["text"]) > limit else ""
        targets, errors = validate_targets(post["text"], "", [aid], {str(aid): override} if override else {})
        if targets and not errors:
            keep.append(targets[0])
    return keep


def generate_group(rule, when):
    """يولّد منشوراً للقاعدة وينشئ مجموعة. يعيد (معرّف المجموعة، العنوان)."""
    titles = json.loads(rule["recent_titles"] or "[]")
    tone = rule["tone"] or "راقي وجذاب"
    if rule["kind"] == "news":
        post = gemini.tech_news(titles, tone)
    else:
        post = gemini.write_post(rule["topic"], tone)
    targets = _build_targets(post, json.loads(rule["account_ids"] or "[]"))
    if not targets:
        raise gemini.GeminiError("لا توجد حسابات صالحة لهذا النص (نص فقط)")
    status = "draft" if rule["require_approval"] else "scheduled"
    gid = db.create_group(post["text"], "", "", when, targets, status=status,
                          source="ai_news" if rule["kind"] == "news" else "ai_topic",
                          meta={"title": post["title"], "rule_id": rule["id"], "sources": post.get("sources", [])})
    return gid, post["title"]


def run_due(now=None):
    now = time.time() if now is None else now
    if not net.online():
        return 0
    if not db.get_gemini_key(config.GEMINI_API_KEY):
        return 0
    made = 0
    now_dt = datetime.datetime.fromtimestamp(now)
    for rule in db.list_rules():
        if not rule["active"]:
            continue
        slots = _slots_due(rule, now_dt)
        if not slots or now - _last_try.get(rule["id"], 0) < RETRY_EVERY:
            continue
        _last_try[rule["id"]] = now
        try:
            gid, title = generate_group(rule, now + 30)
            db.mark_rule_run(rule["id"], slots[-1], title)
            db.log("system", "auto_rule", f"rule #{rule['id']} -> group #{gid}")
            made += 1
        except Exception as e:
            db.log("system", "auto_rule", f"rule #{rule['id']}: {e}", ok=0)
    return made
