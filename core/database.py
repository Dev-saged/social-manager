"""قاعدة البيانات: SQLite بسيطة ومحلية، لا تحتاج خادماً."""
import sqlite3
import json
import time
import random
import config


def _conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS accounts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,            -- instagram / facebook / x / youtube
            username TEXT NOT NULL,
            credentials TEXT NOT NULL,         -- مشفّرة عبر core.accounts
            account_type TEXT DEFAULT 'personal',  -- personal / business
            active INTEGER DEFAULT 1,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS scheduled_posts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            content TEXT,
            media_path TEXT,
            scheduled_at REAL,
            status TEXT DEFAULT 'pending',     -- pending / published / failed
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS targets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            username TEXT
        );
        CREATE TABLE IF NOT EXISTS comment_queue(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            platform TEXT,
            target_username TEXT,
            post_id TEXT,
            post_text TEXT,
            analysis TEXT,
            proposed_comment TEXT,
            status TEXT DEFAULT 'pending',     -- pending/approved/rejected/posted/failed
            release_at REAL,
            posted_at REAL,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS activity_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, platform TEXT, action TEXT, detail TEXT, ok INTEGER
        );
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS rate_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            ts REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rate_events_account_action_ts
            ON rate_events(account_id, action, ts);
        CREATE TABLE IF NOT EXISTS post_groups(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL DEFAULT '',
            media_id TEXT DEFAULT '',
            media_kind TEXT DEFAULT '',        -- image / video / فارغ
            scheduled_at REAL NOT NULL,
            status TEXT DEFAULT 'scheduled',   -- draft / scheduled / cancelled
            source TEXT DEFAULT 'manual',      -- manual / ai_topic / ai_news
            meta TEXT DEFAULT '{}',
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS post_targets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            text_override TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',     -- pending/publishing/published/failed/waiting_network/missed/cancelled
            attempts INTEGER DEFAULT 0,
            due_at REAL NOT NULL,
            next_try_at REAL DEFAULT 0,
            remote_id TEXT DEFAULT '',
            error TEXT DEFAULT '',
            updated_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_post_targets_due ON post_targets(status, due_at);
        CREATE INDEX IF NOT EXISTS idx_post_targets_group ON post_targets(group_id);
        CREATE TABLE IF NOT EXISTS auto_rules(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT '',
            kind TEXT NOT NULL,                -- news / topic
            topic TEXT DEFAULT '',
            account_ids TEXT DEFAULT '[]',
            times TEXT DEFAULT '[]',           -- ["21:00"]
            tone TEXT DEFAULT '',
            require_approval INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1,
            last_slot TEXT DEFAULT '',
            recent_titles TEXT DEFAULT '[]',
            created_at REAL
        );
        """)
    try:
        import os
        os.chmod(config.DB_PATH, 0o600)
    except OSError:
        pass


# ---------- الحسابات ----------
def add_account(platform, username, credentials_json, account_type="personal"):
    from core import accounts as acc_store
    with _conn() as c:
        c.execute(
            "INSERT INTO accounts(platform, username, credentials, account_type, created_at) VALUES(?,?,?,?,?)",
            (platform, username, acc_store.encrypt(credentials_json), account_type, time.time()),
        )


def list_accounts(active_only=False):
    with _conn() as c:
        q = "SELECT * FROM accounts" + (" WHERE active=1" if active_only else "")
        return c.execute(q).fetchall()


def get_account(account_id):
    with _conn() as c:
        return c.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()


def set_account_active(account_id, active):
    with _conn() as c:
        c.execute("UPDATE accounts SET active=? WHERE id=?", (1 if active else 0, account_id))


def delete_account(account_id):
    with _conn() as c:
        c.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        c.execute("DELETE FROM targets WHERE account_id=?", (account_id,))
        c.execute("DELETE FROM scheduled_posts WHERE account_id=?", (account_id,))
        c.execute("DELETE FROM comment_queue WHERE account_id=?", (account_id,))
        c.execute("DELETE FROM rate_events WHERE account_id=?", (account_id,))


def reserve_rate_slot(account_id, action, max_per_hour, now=None):
    if action not in ("comments", "likes") or max_per_hour <= 0:
        return False
    now = time.time() if now is None else now
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM rate_events WHERE ts<=?", (now - 3600,))
        count = c.execute(
            "SELECT COUNT(*) FROM rate_events WHERE account_id=? AND action=? AND ts>?",
            (account_id, action, now - 3600),
        ).fetchone()[0]
        if count >= max_per_hour:
            return False
        c.execute(
            "INSERT INTO rate_events(account_id, action, ts) VALUES(?,?,?)",
            (account_id, action, now),
        )
        return True


# ---------- النشر المجدول ----------
def schedule_post(account_id, content, media_path, scheduled_at):
    with _conn() as c:
        c.execute(
            "INSERT INTO scheduled_posts(account_id, content, media_path, scheduled_at) VALUES(?,?,?,?)",
            (account_id, content, media_path, scheduled_at),
        )


def due_posts():
    with _conn() as c:
        return c.execute(
            "SELECT * FROM scheduled_posts WHERE status='pending' AND scheduled_at<=?", (time.time(),)
        ).fetchall()


def mark_post_status(post_id, status, error=""):
    with _conn() as c:
        c.execute("UPDATE scheduled_posts SET status=?, error=? WHERE id=?", (status, error, post_id))


def list_scheduled_posts():
    with _conn() as c:
        return c.execute("SELECT * FROM scheduled_posts ORDER BY scheduled_at").fetchall()


def delete_scheduled_post(post_id):
    with _conn() as c:
        cur = c.execute("DELETE FROM scheduled_posts WHERE id=? AND status='pending'", (post_id,))
        return cur.rowcount > 0


# ---------- الأهداف (أصدقاء نتفاعل مع منشوراتهم) ----------
def add_target(account_id, username):
    with _conn() as c:
        c.execute("INSERT INTO targets(account_id, username) VALUES(?,?)", (account_id, username))


def list_targets(account_id=None):
    with _conn() as c:
        if account_id:
            return c.execute("SELECT * FROM targets WHERE account_id=?", (account_id,)).fetchall()
        return c.execute("SELECT * FROM targets").fetchall()


def delete_target(target_id):
    with _conn() as c:
        c.execute("DELETE FROM targets WHERE id=?", (target_id,))


# ---------- طابور التعليقات ----------
def add_to_queue(account_id, platform, target_username, post_id, post_text, analysis, proposed_comment):
    with _conn() as c:
        exists = c.execute(
            "SELECT 1 FROM comment_queue WHERE account_id=? AND platform=? AND post_id=?",
            (account_id, platform, str(post_id)),
        ).fetchone()
        if exists:
            return None
        cur = c.execute(
            "INSERT INTO comment_queue(account_id, platform, target_username, post_id, post_text, analysis, proposed_comment) VALUES(?,?,?,?,?,?,?)",
            (account_id, platform, target_username, post_id, post_text, json.dumps(analysis, ensure_ascii=False), proposed_comment),
        )
        return cur.lastrowid


def pending_comments():
    with _conn() as c:
        return c.execute("SELECT * FROM comment_queue WHERE status='pending' ORDER BY id").fetchall()


def approve_comment(comment_id, proposed_comment=None):
    """الموافقة تضيف تأخيراً عشوائياً واقعياً قبل النشر الفعلي."""
    limits_min = 180
    limits_max = 3600
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT platform FROM comment_queue WHERE id=? AND status='pending'",
            (comment_id,),
        ).fetchone()
        if not row:
            return False
        if row["platform"] in config.RATE_LIMITS:
            limits_min = config.RATE_LIMITS[row["platform"]]["min_delay_sec"]
            limits_max = config.RATE_LIMITS[row["platform"]]["max_delay_sec"]
        release_at = time.time() + random.uniform(limits_min, limits_max)
        if proposed_comment is None:
            cur = c.execute(
                "UPDATE comment_queue SET status='approved', release_at=? WHERE id=? AND status='pending'",
                (release_at, comment_id),
            )
        else:
            cur = c.execute(
                "UPDATE comment_queue SET proposed_comment=?, status='approved', release_at=? WHERE id=? AND status='pending'",
                (proposed_comment, release_at, comment_id),
            )
        return cur.rowcount == 1


def reject_comment(comment_id):
    with _conn() as c:
        cur = c.execute(
            "UPDATE comment_queue SET status='rejected' WHERE id=? AND status='pending'",
            (comment_id,),
        )
        return cur.rowcount == 1


def approved_due():
    with _conn() as c:
        return c.execute(
            "SELECT * FROM comment_queue WHERE status='approved' AND release_at<=?", (time.time(),)
        ).fetchall()


def defer_rate_limited_comment(comment_id, now=None):
    now = time.time() if now is None else now
    with _conn() as c:
        row = c.execute(
            "SELECT account_id FROM comment_queue WHERE id=? AND status='approved'",
            (comment_id,),
        ).fetchone()
        if not row:
            return False
        oldest = c.execute(
            "SELECT MIN(ts) FROM rate_events WHERE account_id=? AND action='comments' AND ts>?",
            (row["account_id"], now - 3600),
        ).fetchone()[0]
        release_at = max(now + 60, (oldest + 3600) if oldest is not None else now + 60)
        c.execute(
            "UPDATE comment_queue SET release_at=? WHERE id=? AND status='approved'",
            (release_at, comment_id),
        )
        return True


def mark_comment_status(comment_id, status, error=""):
    with _conn() as c:
        if status == "posted":
            c.execute("UPDATE comment_queue SET status=?, posted_at=?, error='' WHERE id=?", (status, time.time(), comment_id))
        else:
            c.execute("UPDATE comment_queue SET status=?, error=? WHERE id=?", (status, error, comment_id))


def queue_stats():
    with _conn() as c:
        rows = c.execute("SELECT status, COUNT(*) AS n FROM comment_queue GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}


# ---------- السجل والإعدادات ----------
def log(platform, action, detail="", ok=1):
    with _conn() as c:
        c.execute("INSERT INTO activity_log(ts, platform, action, detail, ok) VALUES(?,?,?,?,?)",
                  (time.time(), platform, action, detail[:500], ok))


def recent_logs(limit=100):
    with _conn() as c:
        return c.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def set_setting(key, value):
    with _conn() as c:
        c.execute("INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def get_setting(key, default=""):
    with _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_api_key(value):
    from core import accounts as acc_store
    set_setting("thaura_api_key", acc_store.encrypt({"value": value}))


def get_api_key(default=""):
    from core import accounts as acc_store
    stored = get_setting("thaura_api_key", "")
    if not stored:
        return default
    try:
        return acc_store.decrypt(stored).get("value", default)
    except Exception:
        set_api_key(stored)
        return stored


# ---------- مجموعات النشر (منشور واحد لعدة منصات) ----------
_ACTIVE_TARGET = ("pending", "waiting_network", "publishing")


def create_group(text, media_id, media_kind, scheduled_at, targets, status="scheduled", source="manual", meta=None):
    """targets: [{account_id, platform, text_override}]. يعيد معرّف المجموعة."""
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO post_groups(text, media_id, media_kind, scheduled_at, status, source, meta, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (text, media_id or "", media_kind or "", scheduled_at, status, source,
             json.dumps(meta or {}, ensure_ascii=False), time.time()),
        )
        gid = cur.lastrowid
        for t in targets:
            c.execute(
                "INSERT INTO post_targets(group_id, account_id, platform, text_override, due_at, updated_at) VALUES(?,?,?,?,?,?)",
                (gid, t["account_id"], t["platform"], t.get("text_override") or "", scheduled_at, time.time()),
            )
        return gid


def get_group(group_id):
    with _conn() as c:
        return c.execute("SELECT * FROM post_groups WHERE id=?", (group_id,)).fetchone()


def group_targets(group_id):
    with _conn() as c:
        return c.execute("SELECT * FROM post_targets WHERE group_id=? ORDER BY id", (group_id,)).fetchall()


def list_groups(since=None, until=None, include_cancelled=False):
    q, args = "SELECT * FROM post_groups WHERE 1=1", []
    if since is not None:
        q += " AND scheduled_at>=?"
        args.append(since)
    if until is not None:
        q += " AND scheduled_at<?"
        args.append(until)
    if not include_cancelled:
        q += " AND status!='cancelled'"
    with _conn() as c:
        return c.execute(q + " ORDER BY scheduled_at, id", args).fetchall()


def update_group(group_id, text=None, scheduled_at=None, status=None, overrides=None):
    """يعدّل مجموعة لم يبدأ نشرها. overrides: {account_id: نص}."""
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        g = c.execute("SELECT * FROM post_groups WHERE id=?", (group_id,)).fetchone()
        if not g or g["status"] == "cancelled":
            return False
        busy = c.execute(
            "SELECT 1 FROM post_targets WHERE group_id=? AND status IN ('publishing','published')", (group_id,)
        ).fetchone()
        if busy:
            return False
        if text is not None:
            c.execute("UPDATE post_groups SET text=? WHERE id=?", (text, group_id))
        if status is not None:
            c.execute("UPDATE post_groups SET status=? WHERE id=?", (status, group_id))
        if scheduled_at is not None:
            c.execute("UPDATE post_groups SET scheduled_at=? WHERE id=?", (scheduled_at, group_id))
            c.execute(
                "UPDATE post_targets SET due_at=?, next_try_at=0, status='pending', error='' WHERE group_id=? AND status IN ('pending','waiting_network','missed','failed')",
                (scheduled_at, group_id),
            )
        for account_id, override in (overrides or {}).items():
            c.execute(
                "UPDATE post_targets SET text_override=? WHERE group_id=? AND account_id=?",
                (override or "", group_id, int(account_id)),
            )
        return True


def cancel_group(group_id):
    """يلغي الأهداف التي لم تُنشر بعد. يعيد (تم، معرّف الملف إن صار بلا استخدام)."""
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        g = c.execute("SELECT * FROM post_groups WHERE id=?", (group_id,)).fetchone()
        if not g:
            return False, ""
        c.execute(
            "UPDATE post_targets SET status='cancelled' WHERE group_id=? AND status IN ('pending','waiting_network','missed','failed')",
            (group_id,),
        )
        still = c.execute(
            "SELECT 1 FROM post_targets WHERE group_id=? AND status IN ('publishing','published')", (group_id,)
        ).fetchone()
        if not still:
            c.execute("UPDATE post_groups SET status='cancelled' WHERE id=?", (group_id,))
        media = g["media_id"] if not still else ""
        return True, media


def retry_target(target_id):
    with _conn() as c:
        now = time.time()
        cur = c.execute(
            "UPDATE post_targets SET status='pending', attempts=0, due_at=?, next_try_at=0, error='', updated_at=? "
            "WHERE id=? AND status IN ('failed','missed')",
            (now, now, target_id),
        )
        return cur.rowcount == 1


def claim_due_targets(now=None, grace_sec=12 * 3600):
    """يحجز الأهداف المستحقة ذرّياً (publishing) قبل الإرسال. المتأخر عن المهلة يصير missed."""
    now = time.time() if now is None else now
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute(
            "SELECT t.* FROM post_targets t JOIN post_groups g ON g.id=t.group_id "
            "WHERE g.status='scheduled' AND t.status IN ('pending','waiting_network') "
            "AND t.due_at<=? AND t.next_try_at<=? ORDER BY t.due_at, t.id",
            (now, now),
        ).fetchall()
        claimed = []
        for r in rows:
            if now - r["due_at"] > grace_sec:
                c.execute(
                    "UPDATE post_targets SET status='missed', error='فات الموعد ولم يتوفر إنترنت خلال المهلة', updated_at=? WHERE id=?",
                    (now, r["id"]),
                )
                continue
            c.execute(
                "UPDATE post_targets SET status='publishing', attempts=attempts+1, updated_at=? WHERE id=? AND status IN ('pending','waiting_network')",
                (now, r["id"]),
            )
            claimed.append(r)
        return claimed


def recover_stuck_targets(now=None, older_than=900):
    """هدف بقي publishing بعد قتل العملية: لا نعيد الإرسال أبداً لأن النتيجة غير معروفة."""
    now = time.time() if now is None else now
    with _conn() as c:
        cur = c.execute(
            "UPDATE post_targets SET status='failed', error='توقفت العملية أثناء النشر، تحقق من الحساب قبل إعادة المحاولة', updated_at=? "
            "WHERE status='publishing' AND updated_at<?",
            (now, now - older_than),
        )
        return cur.rowcount


def finish_target(target_id, status, error="", remote_id="", next_try_at=0):
    with _conn() as c:
        c.execute(
            "UPDATE post_targets SET status=?, error=?, remote_id=?, next_try_at=?, updated_at=? WHERE id=?",
            (status, error[:500], remote_id, next_try_at, time.time(), target_id),
        )


def group_media_if_done(group_id):
    """يعيد معرّف الملف إذا نُشر كل شيء أو أُلغي (لا يلزم الملف بعد الآن)."""
    with _conn() as c:
        g = c.execute("SELECT media_id FROM post_groups WHERE id=?", (group_id,)).fetchone()
        if not g or not g["media_id"]:
            return ""
        open_ = c.execute(
            "SELECT 1 FROM post_targets WHERE group_id=? AND status NOT IN ('published','cancelled')", (group_id,)
        ).fetchone()
        return "" if open_ else g["media_id"]


def clear_group_media(group_id):
    with _conn() as c:
        c.execute("UPDATE post_groups SET media_id='' WHERE id=?", (group_id,))


def referenced_media_ids():
    """ملفات ما زالت لازمة: مجموعات فيها هدف غير منتهٍ أو فاشل، أو منشورات قديمة معلّقة."""
    with _conn() as c:
        ids = {r[0] for r in c.execute(
            "SELECT DISTINCT g.media_id FROM post_groups g JOIN post_targets t ON t.group_id=g.id "
            "WHERE g.media_id!='' AND g.status!='cancelled' AND t.status NOT IN ('published','cancelled')"
        )}
        ids |= {r[0] for r in c.execute("SELECT media_id FROM post_groups WHERE status='draft' AND media_id!=''")}
        for r in c.execute("SELECT media_path FROM scheduled_posts WHERE status='pending' AND media_path IS NOT NULL"):
            ids.add(str(r[0]).replace("\\", "/").rsplit("/", 1)[-1])
        return ids


def target_counts():
    with _conn() as c:
        rows = c.execute(
            "SELECT t.status, COUNT(*) AS n FROM post_targets t JOIN post_groups g ON g.id=t.group_id "
            "WHERE g.status!='cancelled' GROUP BY t.status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}


# ---------- Gemini والقواعد المتكررة ----------
def set_gemini_key(value):
    from core import accounts as acc_store
    set_setting("gemini_api_key", acc_store.encrypt({"value": value}))


def get_gemini_key(default=""):
    from core import accounts as acc_store
    stored = get_setting("gemini_api_key", "")
    if not stored:
        return default
    try:
        return acc_store.decrypt(stored).get("value", default)
    except Exception:
        return default


def add_rule(name, kind, topic, account_ids, times, tone, require_approval):
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO auto_rules(name, kind, topic, account_ids, times, tone, require_approval, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (name, kind, topic, json.dumps(account_ids), json.dumps(times), tone, 1 if require_approval else 0, time.time()),
        )
        return cur.lastrowid


def list_rules():
    with _conn() as c:
        return c.execute("SELECT * FROM auto_rules ORDER BY id").fetchall()


def get_rule(rule_id):
    with _conn() as c:
        return c.execute("SELECT * FROM auto_rules WHERE id=?", (rule_id,)).fetchone()


def update_rule(rule_id, **fields):
    allowed = {"name", "topic", "tone", "require_approval", "active", "account_ids", "times"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("account_ids", "times"):
            v = json.dumps(v)
        elif k in ("require_approval", "active"):
            v = 1 if v else 0
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return False
    with _conn() as c:
        cur = c.execute(f"UPDATE auto_rules SET {', '.join(sets)} WHERE id=?", (*args, rule_id))
        return cur.rowcount == 1


def delete_rule(rule_id):
    with _conn() as c:
        c.execute("DELETE FROM auto_rules WHERE id=?", (rule_id,))


def mark_rule_run(rule_id, slot, title):
    with _conn() as c:
        row = c.execute("SELECT recent_titles FROM auto_rules WHERE id=?", (rule_id,)).fetchone()
        titles = json.loads(row["recent_titles"]) if row else []
        if title:
            titles = (titles + [title])[-30:]
        c.execute("UPDATE auto_rules SET last_slot=?, recent_titles=? WHERE id=?",
                  (slot, json.dumps(titles, ensure_ascii=False), rule_id))
