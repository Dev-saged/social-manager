"""نسخة احتياطية كاملة مشفّرة بعبارة مرور منك: الحسابات بمفاتيحها، الإعدادات، القواعد، والمجدول.

التشفير: PBKDF2-SHA256 (390 ألف دورة) لاشتقاق مفتاح Fernet من عبارتك. بدون العبارة لا يُقرأ الملف.
"""
import base64
import json
import os
import time
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import config
from core import accounts as acc_store
from core import database as db
from core.errors import AppError

ITER = 390_000
SECRET_SETTINGS = ("oxion_api_key", "gemini_api_key", "github_token")
SKIP_SETTINGS = ("hb_at", "sched_started_at", "oxion_models", "gemini_models", "deps_checked_at")


def _fernet(passphrase, salt):
    if len(passphrase or "") < 8:
        raise AppError("invalid_input", "عبارة المرور 8 أحرف على الأقل")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase.encode())))


def _payload():
    accounts = [{"id": a["id"], "platform": a["platform"], "username": a["username"], "type": a["account_type"],
                 "active": a["active"], "credentials": acc_store.decrypt(a["credentials"])} for a in db.list_accounts()]
    settings = {}
    with db._conn() as c:
        for r in c.execute("SELECT key, value FROM settings"):
            if r["key"] in SKIP_SETTINGS:
                continue
            settings[r["key"]] = db.get_secret(r["key"]) if r["key"] in SECRET_SETTINGS else r["value"]
    rules = [dict(r) for r in db.list_rules()]
    replies = [dict(r) for r in db.list_reply_rules()]
    now = time.time()
    groups = []
    for g in db.list_groups(now - 3600, None):
        if g["status"] not in ("draft", "scheduled"):
            continue
        ts = db.group_targets(g["id"])
        groups.append({"text": g["text"], "scheduled_at": g["scheduled_at"], "status": g["status"], "source": g["source"],
                       "meta": g["meta"], "targets": [{"account_id": t["account_id"], "platform": t["platform"],
                                                        "text_override": t["text_override"]} for t in ts if t["status"] == "pending"]})
    return {"accounts": accounts, "settings": settings, "rules": rules, "replies": replies, "groups": groups}


def export(passphrase):
    salt = os.urandom(16)
    data = json.dumps(_payload(), ensure_ascii=False).encode()
    token = _fernet(passphrase, salt).encrypt(data).decode()
    return {"app": "social-manager", "kind": "full-encrypted", "version": config.APP_VERSION,
            "exported_at": time.time(), "kdf": "pbkdf2-sha256", "iter": ITER,
            "salt": base64.b64encode(salt).decode(), "data": token}


def restore(passphrase, blob):
    if not isinstance(blob, dict) or blob.get("kind") != "full-encrypted":
        raise AppError("invalid_input", "الملف ليس نسخة كاملة مشفّرة")
    try:
        raw = _fernet(passphrase, base64.b64decode(blob["salt"])).decrypt(blob["data"].encode())
    except (InvalidToken, KeyError, ValueError):
        raise AppError("invalid_input", "عبارة المرور خاطئة أو الملف تالف")
    p = json.loads(raw)
    current = {(a["platform"], a["username"]): a["id"] for a in db.list_accounts()}
    idmap, added = {}, 0
    for a in p.get("accounts", []):
        key = (a["platform"], a["username"])
        if key not in current:
            db.add_account(a["platform"], a["username"], a["credentials"], a["type"])
            current = {(x["platform"], x["username"]): x["id"] for x in db.list_accounts()}
            added += 1
        idmap[a["id"]] = current[key]
    for k, v in (p.get("settings") or {}).items():
        if k in SECRET_SETTINGS:
            db.set_secret(k, v)
        else:
            db.set_setting(k, v)
    n_rules = n_replies = n_groups = 0
    for r in p.get("rules", []):
        ids = [idmap[i] for i in json.loads(r.get("account_ids") or "[]") if i in idmap]
        if ids:
            db.add_rule(r["name"], r["kind"], r["topic"], ids, json.loads(r["times"] or "[]"), r["tone"], r["require_approval"])
            n_rules += 1
    for r in p.get("replies", []):
        if r["account_id"] in idmap:
            db.add_reply_rule(idmap[r["account_id"]], r["name"], r["media_id"], json.loads(r["keywords"] or "[]"),
                              r["any_comment"], r["whole_word"], json.loads(r["public_replies"] or "[]"), r["dm_text"], r["dm_link"])
            n_replies += 1
    now = time.time()
    for g in p.get("groups", []):
        targets = [dict(t, account_id=idmap[t["account_id"]]) for t in g["targets"] if t["account_id"] in idmap]
        if not targets or (g["status"] == "scheduled" and g["scheduled_at"] < now):
            continue
        db.create_group(g["text"], "", "", g["scheduled_at"], targets, status=g["status"], source=g["source"],
                        meta=json.loads(g["meta"] or "{}"))
        n_groups += 1
    return {"accounts": added, "rules": n_rules, "replies": n_replies, "groups": n_groups}
