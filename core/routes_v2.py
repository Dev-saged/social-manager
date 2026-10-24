"""مسارات النشر المتعدد (مجموعات) والصحة. تُسجَّل في core/server.py."""
import datetime
import json
import time
from flask import Blueprint, request, jsonify
import config
from core import database as db
from core import media
from core import net
from core import errors as errors_mod
import platforms

bp = Blueprint("v2", __name__)


def _parse_when(value):
    """يقبل ثواني Unix أو نص ISO محلي مثل 2026-10-24T21:00. يعيد None إذا غير صالح."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return datetime.datetime.fromisoformat(value.strip()).timestamp()
        except ValueError:
            return None
    return None


def _state(group, targets):
    if group["status"] in ("draft", "cancelled"):
        return group["status"]
    st = {t["status"] for t in targets}
    if not st or st <= {"cancelled"}:
        return "cancelled"
    if st <= {"published", "cancelled"}:
        return "done"
    if "publishing" in st:
        return "publishing"
    if "needs_you" in st:
        return "needs_you"
    if "waiting_network" in st:
        return "waiting"
    if st & {"published"} and st & {"failed", "missed"}:
        return "partial"
    if st <= {"failed", "missed", "cancelled"}:
        return "failed"
    return "scheduled"


def group_view(group):
    targets = db.group_targets(group["id"])
    accs = db.list_accounts()
    names = {a["id"]: a["username"] for a in accs}
    types = {a["id"]: a["account_type"] for a in accs}
    try:
        meta = json.loads(group["meta"] or "{}")
    except ValueError:
        meta = {}
    return {
        "id": group["id"], "text": group["text"], "media_id": group["media_id"],
        "media_kind": group["media_kind"], "scheduled_at": group["scheduled_at"],
        "status": group["status"], "state": _state(group, targets),
        "source": group["source"], "meta": meta,
        "targets": [{
            "id": t["id"], "account_id": t["account_id"], "platform": t["platform"],
            "username": names.get(t["account_id"], ""), "status": t["status"],
            "attempts": t["attempts"], "error": t["error"], "text_override": t["text_override"],
            "next_try_at": t["next_try_at"], "code": t["error_code"] or "",
            "hint": errors_mod.CODES.get(t["error_code"] or "", ("", False, ""))[2] if t["error_code"] else "",
            "account_type": types.get(t["account_id"], ""),
        } for t in targets],
    }


def validate_targets(text, media_id, account_ids, overrides):
    """يتحقق من كل حساب مقابل إمكانيات منصته. يعيد (الأهداف، قائمة الأخطاء)."""
    platforms._load()
    kind = media.kind_of(media_id) if media_id else ""
    targets, errors = [], []
    for aid in account_ids:
        acc = db.get_account(aid)
        if not acc:
            errors.append(f"الحساب {aid} غير موجود")
            continue
        if not acc["active"]:
            errors.append(f"{acc['username']}: الحساب غير مفعّل")
            continue
        plat = acc["platform"]
        cls = platforms.ADAPTERS.get(plat)
        if not cls:
            errors.append(f"{acc['username']}: منصة غير مدعومة")
            continue
        label = f"{plat} · {acc['username']}"
        if plat == "instagram" and acc["account_type"] == "business":
            errors.append(f"{label}: حساب Business القديم يحتاج رابط وسائط عاماً، أضف الحساب بالوضع الرسمي")
            continue
        own = (overrides.get(str(aid)) or overrides.get(aid) or "").strip()
        errors.extend(f"{label}: {why}" for why in cls.preflight(own or text, kind, acc["account_type"]))
        targets.append({"account_id": aid, "platform": plat, "text_override": own})
    return targets, errors


def _pct(vals, p):
    if not vals:
        return 0.0
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(p * (len(vals) - 1))))]


@bp.get("/api/health")
def health():
    """نبض الحياة: آخر دورة للمجدول، المتأخر، وسجل التأخير."""
    from core import scheduler
    now = time.time()
    hb = float(db.get_setting("hb_at", "0") or 0)
    delays = db.recent_delays(30)
    vals = [d["delay"] for d in delays]
    return jsonify({"ok": True, "version": config.APP_VERSION, "online": net.online(), "scheduler": scheduler.running(),
                    "time": now, "targets": db.target_counts(), "hb_at": hb, "hb_age": (now - hb) if hb else None,
                    "started_at": float(db.get_setting("sched_started_at", "0") or 0), "overdue": db.overdue_count(now),
                    "delays": delays, "delay_avg": (sum(vals) / len(vals)) if vals else 0.0, "delay_p95": _pct(vals, .95),
                    "schema": db.schema_version()})


@bp.post("/api/health/reconcile")
def reconcile():
    """مطابقة عند الفتح: يشغّل المجدول إن توقف، ويستدرك المستحق فوراً بدل انتظار الدورة."""
    import threading
    from core import scheduler
    restarted = False
    if not scheduler.running():
        try:
            scheduler.start()
            restarted = True
        except Exception as e:
            db.log("system", "reconcile", str(e), ok=0)
    before = db.overdue_count()
    threading.Thread(target=scheduler.tick, daemon=True).start()
    return jsonify({"ok": True, "overdue": before, "restarted": restarted})


def _check_account(acc):
    try:
        res = platforms.get(acc["platform"], acc).check()
    except Exception as e:
        res = errors_mod.classify(e)
        res.update(ok=False)
    db.set_health(acc["id"], res)
    return res


_checking = {"running": False}


@bp.get("/api/accounts/health")
def accounts_health():
    hl = db.list_health()
    out = []
    for a in db.list_accounts():
        h = hl.get(a["id"]) or {}
        out.append({"id": a["id"], "platform": a["platform"], "username": a["username"], "type": a["account_type"],
                    "active": bool(a["active"]), "ok": h.get("ok"), "code": h.get("code", ""), "message": h.get("message", ""),
                    "hint": h.get("hint", ""), "detail": h.get("detail", ""), "checked_at": h.get("checked_at")})
    return jsonify({"running": _checking["running"], "accounts": out})


@bp.post("/api/accounts/check-all")
def accounts_check_all():
    """يفحص كل الحسابات في الخلفية. الحسابات الشخصية (instagrapi) تُستثنى إلا إن طلبتها لتجنب تنبيهات الدخول."""
    import threading
    include_personal = bool((request.get_json(silent=True) or {}).get("include_personal"))
    if _checking["running"]:
        return jsonify({"started": False})
    _checking["running"] = True

    def run():
        try:
            for a in db.list_accounts(active_only=True):
                if a["platform"] == "instagram" and a["account_type"] == "personal" and not include_personal:
                    continue
                _check_account(a)
        finally:
            _checking["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"started": True})


@bp.post("/api/accounts/<int:account_id>/ig-login")
def ig_login(account_id):
    """تسجيل دخول Instagram الشخصي تفاعلياً: إن طلب رمزاً تعرضه الواجهة وتنتظر إدخالك."""
    import threading
    from platforms import instagram
    acc = db.get_account(account_id)
    if not acc or acc["platform"] != "instagram":
        return jsonify({"error": "حساب Instagram غير موجود"}), 404
    if instagram.login_state(account_id).get("state") in ("running", "need_code", "verifying"):
        return jsonify({"started": False})
    instagram._set_login(account_id, state="running", kind="", message="جارٍ تسجيل الدخول...", code="")

    def run():
        try:
            ad = platforms.get("instagram", acc)
            if ad.mode == "official" and not ad._has_fallback():
                res = ad.check()
                if not res["ok"]:
                    raise errors_mod.AppError(res["code"], res.get("detail", ""))
            else:
                ad.mode = "personal"
                ad.interactive = True
                ad._personal_client()
            res = {"ok": True, "code": "", "message": "تم تسجيل الدخول وحُفظت الجلسة", "hint": "", "detail": ""}
            instagram._set_login(account_id, state="ok", message=res["message"])
        except Exception as e:
            res = errors_mod.classify(e)
            res["ok"] = False
            instagram._set_login(account_id, state="failed", message=errors_mod.text_of(res), hint=res["hint"])
        db.set_health(account_id, res)
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"started": True})


@bp.get("/api/accounts/<int:account_id>/ig-login")
def ig_login_state(account_id):
    from platforms import instagram
    return jsonify(instagram.login_state(account_id))


@bp.post("/api/accounts/<int:account_id>/ig-code")
def ig_code(account_id):
    from platforms import instagram
    code = str((request.get_json(force=True) or {}).get("code", "")).strip()
    if not code.isdigit() or not 4 <= len(code) <= 8:
        return jsonify({"error": "الرمز أرقام من 4 إلى 8"}), 400
    if not instagram.submit_code(account_id, code):
        return jsonify({"error": "لا يوجد طلب رمز الآن"}), 409
    return jsonify({"ok": True})


@bp.post("/api/relay/check")
def relay_check():
    from core import relay
    if not relay.configured():
        return jsonify({"ok": False, "error": "أدخل المستودع ورمز GitHub أولاً"})
    try:
        return jsonify(dict(relay.probe(), ok=True))
    except Exception as e:
        info = errors_mod.classify(e)
        return jsonify({"ok": False, "error": errors_mod.text_of(info), "hint": info["hint"]})


@bp.get("/api/deps")
def deps_status():
    from core import deps
    return jsonify(deps.status())


@bp.get("/api/deps/check")
def deps_check():
    from core import deps
    return jsonify(deps.check_each())


@bp.post("/api/deps/reinstall")
def deps_reinstall():
    from core import deps
    spec = (request.get_json(force=True) or {}).get("spec", "")
    if spec not in deps.optional_specs():
        return jsonify({"error": "هذه المكتبة مضمّنة في التطبيق ولا تُعاد من هنا"}), 400
    return jsonify({"started": deps.start(specs=[spec], force=True)})


@bp.post("/api/deps/install")
def deps_install():
    from core import deps
    upgrade = bool((request.get_json(silent=True) or {}).get("upgrade"))
    return jsonify({"started": deps.start(upgrade=upgrade)})


@bp.get("/api/platforms")
def platform_caps():
    return jsonify(platforms.capabilities())


@bp.post("/api/groups")
def create_group():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    media_id = (data.get("media_id") or "").strip()
    account_ids = [int(a) for a in (data.get("account_ids") or []) if str(a).isdigit()]
    overrides = data.get("overrides") or {}
    when = _parse_when(data.get("scheduled_at"))
    status = "draft" if data.get("draft") else "scheduled"
    if not account_ids:
        return jsonify({"error": "اختر منصة واحدة على الأقل"}), 400
    if not text and not media_id:
        return jsonify({"error": "اكتب نصاً أو أرفق ملفاً"}), 400
    if when is None:
        return jsonify({"error": "وقت النشر غير صالح"}), 400
    if when < time.time() - 60 and status == "scheduled":
        return jsonify({"error": "وقت النشر في الماضي"}), 400
    if media_id:
        import os
        if not media.valid_id(media_id) or not os.path.isfile(media.path_for(media_id)):
            return jsonify({"error": "الملف المرفوع غير موجود"}), 400
    targets, errors = validate_targets(text, media_id, account_ids, overrides if isinstance(overrides, dict) else {})
    if errors:
        return jsonify({"error": "؛ ".join(errors), "errors": errors}), 400
    gid = db.create_group(text, media_id, media.kind_of(media_id), when, targets, status=status,
                          source=data.get("source", "manual"), meta=data.get("meta") or {})
    return jsonify({"ok": True, "id": gid})


@bp.get("/api/groups")
def list_groups():
    since = _parse_when(request.args.get("from", type=float) or request.args.get("from"))
    until = _parse_when(request.args.get("to", type=float) or request.args.get("to"))
    return jsonify([group_view(g) for g in db.list_groups(since, until)])


@bp.patch("/api/groups/<int:group_id>")
def patch_group(group_id):
    data = request.get_json(force=True)
    g = db.get_group(group_id)
    if not g:
        return jsonify({"error": "المنشور غير موجود"}), 404
    when = _parse_when(data["scheduled_at"]) if "scheduled_at" in data else None
    if "scheduled_at" in data and when is None:
        return jsonify({"error": "وقت النشر غير صالح"}), 400
    status = None
    if data.get("approve") and g["status"] == "draft":
        status = "scheduled"
        if (when if when is not None else g["scheduled_at"]) < time.time():
            when = time.time() + 30
    text = data.get("text")
    if text is not None:
        text = text.strip()
    overrides = data.get("overrides") or {}
    effective = text if text is not None else g["text"]
    ids = [t["account_id"] for t in db.group_targets(group_id) if t["status"] != "cancelled"]
    merged = {str(t["account_id"]): t["text_override"] for t in db.group_targets(group_id)}
    merged.update({str(k): v for k, v in overrides.items()})
    _, errors = validate_targets(effective, g["media_id"], ids, merged)
    if errors:
        return jsonify({"error": "؛ ".join(errors), "errors": errors}), 400
    if not db.update_group(group_id, text=text, scheduled_at=when, status=status, overrides=overrides):
        return jsonify({"error": "لا يمكن تعديل منشور بدأ نشره أو أُلغي"}), 409
    return jsonify({"ok": True})


@bp.delete("/api/groups/<int:group_id>")
def delete_group(group_id):
    ok, media_id = db.cancel_group(group_id)
    if not ok:
        return jsonify({"error": "المنشور غير موجود"}), 404
    if media_id:
        media.delete(media_id)
        db.clear_group_media(group_id)
    return jsonify({"ok": True})


@bp.post("/api/group-targets/<int:target_id>/done")
def confirm_target(target_id):
    """أكدت أنك نشرت المنشور بنفسك (فيسبوك الشخصي)."""
    t = db.get_target(target_id)
    if not t or not db.confirm_manual_target(target_id):
        return jsonify({"error": "هذا الهدف لا ينتظر تأكيدك"}), 409
    db.record_delay(target_id, t["platform"], t["due_at"])
    media_id = db.group_media_if_done(t["group_id"])
    if media_id:
        media.delete(media_id)
        db.clear_group_media(t["group_id"])
    return jsonify({"ok": True})


@bp.post("/api/group-targets/<int:target_id>/retry")
def retry_target(target_id):
    if not db.retry_target(target_id):
        return jsonify({"error": "لا يمكن إعادة محاولة هذا الهدف"}), 409
    return jsonify({"ok": True})


# ---------- الذكاء (Gemini) ----------
@bp.post("/api/ai/generate")
def ai_generate():
    from core import gemini
    data = request.get_json(force=True)
    mode = data.get("mode", "topic")
    tone = (data.get("tone") or "راقي وجذاب").strip()
    try:
        if mode == "news":
            titles = [g["meta"].get("title", "") for g in (group_view(x) for x in db.list_groups(time.time() - 3 * 86400, None)) if g["source"] == "ai_news"]
            return jsonify(gemini.tech_news([t for t in titles if t][-15:], tone))
        topic = (data.get("topic") or "").strip()
        if not topic:
            return jsonify({"error": "اكتب موضوع المنشور"}), 400
        return jsonify(gemini.write_post(topic, tone))
    except gemini.GeminiError as e:
        return jsonify({"error": str(e)}), 502


@bp.get("/api/ai/models")
def ai_models():
    """النماذج الحية لكل مزوّد مع الترشيح التلقائي (الأرخص والأقوى لكل دور)."""
    from core import gemini, oxion
    force = request.args.get("force") == "1"
    out = {"oxion": {"list": [], "picks": {}, "error": ""}, "gemini": {"list": [], "picks": {}, "error": ""}}
    if oxion.key():
        try:
            lst = oxion.models(force)
            out["oxion"]["list"] = lst
            out["oxion"]["picks"] = {r: oxion.recommend(lst, n) for r, n in
                                     (("text", ()), ("web", ("web_search",)), ("vision", ("vision",)))}
        except Exception as e:
            out["oxion"]["error"] = errors_mod.text_of(errors_mod.classify(e))
    if db.get_gemini_key(config.GEMINI_API_KEY):
        try:
            lst = gemini.models(force)
            out["gemini"]["list"] = [m["id"] for m in lst]
            out["gemini"]["picks"] = {r: gemini.recommend(lst, r) for r in ("text", "image", "video")}
        except Exception as e:
            out["gemini"]["error"] = str(e)
    return jsonify(out)


@bp.post("/api/ai/test")
def ai_test():
    """تجربة سريعة لمزوّد واحد: يعرض النموذج المستخدم وسبب الفشل إن وُجد."""
    from core import gemini, oxion
    which = (request.get_json(force=True) or {}).get("provider", "oxion")
    try:
        if which == "oxion":
            text, _, model = oxion.chat([{"role": "user", "content": "قل: جاهز"}], temperature=0)
        else:
            text, _ = gemini.call("أجب بكلمة واحدة", "قل: جاهز", temperature=0)
            model = gemini._model()
        return jsonify({"ok": True, "model": model, "reply": text[:80]})
    except Exception as e:
        info = errors_mod.classify(e)
        return jsonify({"ok": False, "error": errors_mod.text_of(info), "hint": info["hint"]})


@bp.post("/api/ai/media")
def ai_media():
    from core import media_gen
    data = request.get_json(force=True)
    kind = data.get("kind")
    prompt = (data.get("prompt") or "").strip()
    if kind not in ("image", "video"):
        return jsonify({"error": "النوع صورة أو فيديو"}), 400
    if not prompt:
        return jsonify({"error": "اكتب الفكرة أو نص المنشور أولاً"}), 400
    if not db.get_gemini_key(config.GEMINI_API_KEY):
        return jsonify({"error": "توليد الصور والفيديو يحتاج مفتاح Gemini"}), 400
    return jsonify({"job": media_gen.start(kind, prompt[:2000], data.get("aspect") or "9:16")})


@bp.get("/api/ai/media/<jid>")
def ai_media_job(jid):
    from core import media_gen
    return jsonify(media_gen.job(jid))


def _rule_view(r):
    return {"id": r["id"], "name": r["name"], "kind": r["kind"], "topic": r["topic"],
            "account_ids": json.loads(r["account_ids"] or "[]"), "times": json.loads(r["times"] or "[]"),
            "tone": r["tone"], "require_approval": bool(r["require_approval"]), "active": bool(r["active"]),
            "last_slot": r["last_slot"]}


def _rule_fields(data):
    kind = data.get("kind")
    if kind not in ("news", "topic"):
        return None, "نوع القاعدة: news أو topic"
    times = [t for t in (data.get("times") or []) if isinstance(t, str) and len(t) == 5 and t[2] == ":" and t[:2].isdigit() and t[3:].isdigit() and int(t[:2]) < 24 and int(t[3:]) < 60]
    if not times:
        return None, "حدّد وقتاً واحداً على الأقل بصيغة HH:MM"
    ids = [int(a) for a in (data.get("account_ids") or []) if str(a).isdigit()]
    if not ids:
        return None, "اختر حساباً واحداً على الأقل"
    if kind == "topic" and not (data.get("topic") or "").strip():
        return None, "اكتب موضوع القاعدة"
    return {"kind": kind, "times": times, "account_ids": ids}, ""


@bp.get("/api/rules")
def list_rules():
    return jsonify([_rule_view(r) for r in db.list_rules()])


@bp.post("/api/rules")
def add_rule():
    data = request.get_json(force=True)
    f, err = _rule_fields(data)
    if err:
        return jsonify({"error": err}), 400
    rid = db.add_rule((data.get("name") or "").strip(), f["kind"], (data.get("topic") or "").strip(),
                      f["account_ids"], f["times"], (data.get("tone") or "").strip(),
                      data.get("require_approval", True))
    return jsonify({"ok": True, "id": rid})


@bp.patch("/api/rules/<int:rule_id>")
def patch_rule(rule_id):
    data = request.get_json(force=True)
    allowed = {k: data[k] for k in ("name", "topic", "tone", "require_approval", "active", "account_ids", "times") if k in data}
    if not db.update_rule(rule_id, **allowed):
        return jsonify({"error": "القاعدة غير موجودة"}), 404
    return jsonify({"ok": True})


@bp.delete("/api/rules/<int:rule_id>")
def delete_rule(rule_id):
    db.delete_rule(rule_id)
    return jsonify({"ok": True})


@bp.post("/api/rules/<int:rule_id>/run")
def run_rule(rule_id):
    from core import auto_rules, gemini
    rule = db.get_rule(rule_id)
    if not rule:
        return jsonify({"error": "القاعدة غير موجودة"}), 404
    try:
        gid, title = auto_rules.generate_group(rule, time.time() + 60)
    except gemini.GeminiError as e:
        return jsonify({"error": str(e)}), 502
    db.mark_rule_run(rule_id, rule["last_slot"], title)
    return jsonify({"ok": True, "group_id": gid})


@bp.post("/api/backup/full")
def backup_full():
    from core import backup
    try:
        return jsonify(backup.export((request.get_json(force=True) or {}).get("passphrase", "")))
    except errors_mod.AppError as e:
        return jsonify({"error": str(e)}), 400


@bp.post("/api/backup/full/restore")
def backup_restore():
    from core import backup
    data = request.get_json(force=True) or {}
    try:
        return jsonify(dict(backup.restore(data.get("passphrase", ""), data.get("blob")), ok=True))
    except errors_mod.AppError as e:
        return jsonify({"error": str(e)}), 400
