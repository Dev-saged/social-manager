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
        CREATE TABLE IF NOT EXISTS reply_rules(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            name TEXT DEFAULT '',
            media_id TEXT DEFAULT '',          -- فارغ = كل المنشورات
            keywords TEXT DEFAULT '[]',
            any_comment INTEGER DEFAULT 0,
            whole_word INTEGER DEFAULT 1,
            public_replies TEXT DEFAULT '[]',
            dm_text TEXT DEFAULT '',
            dm_link TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS reply_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER, account_id INTEGER NOT NULL,
            comment_id TEXT NOT NULL, media_id TEXT, user_id TEXT, username TEXT, comment_text TEXT,
            public_text TEXT DEFAULT '', dm_text TEXT DEFAULT '',
            public_status TEXT DEFAULT 'none',  -- none/pending/sending/sent/failed
            dm_status TEXT DEFAULT 'none',
            status TEXT DEFAULT 'queued',       -- queued/done
            attempts INTEGER DEFAULT 0,
            release_at REAL, error TEXT DEFAULT '',
            created_at REAL, updated_at REAL,
            UNIQUE(account_id, comment_id)
        );
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
        _migrate(c)
    try:
        import os
        os.chmod(config.DB_PATH, 0o600)
    except OSError:
        pass


# ترحيلات مرقّمة: تُضاف في آخر القائمة فقط، ولا يُعدَّل القديم منها
MIGRATIONS = [
    "ALTER TABLE post_targets ADD COLUMN error_code TEXT DEFAULT ''",
    """CREATE TABLE IF NOT EXISTS account_health(
        account_id INTEGER PRIMARY KEY, ok INTEGER, code TEXT DEFAULT '', message TEXT DEFAULT '',
        hint TEXT DEFAULT '', detail TEXT DEFAULT '', checked_at REAL)""",
    """CREATE TABLE IF NOT EXISTS publish_delays(
        id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER, platform TEXT, due_at REAL, done_at REAL)""",
    "DELETE FROM settings WHERE key='thaura_api_key'",
    "ALTER TABLE post_groups ADD COLUMN request_key TEXT DEFAULT NULL",
    "ALTER TABLE post_groups ADD COLUMN request_digest TEXT DEFAULT ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_group_request_key ON post_groups(request_key) WHERE request_key IS NOT NULL",
    """UPDATE post_targets SET status='outcome_unknown', error_code='outcome_unknown',
        error='أهداف مكررة كانت قيد النشر؛ افحص الحساب قبل أي إعادة إرسال'
        WHERE status='publishing' AND EXISTS(SELECT 1 FROM post_targets other
        WHERE other.group_id=post_targets.group_id AND other.account_id=post_targets.account_id
        AND other.id!=post_targets.id AND other.status IN ('publishing','outcome_unknown'))""",
    """UPDATE post_targets SET status='cancelled', error='أُلغي هدف مكرر للحساب نفسه'
       WHERE status IN ('pending','waiting_network','failed','missed','needs_you') AND EXISTS (
         SELECT 1 FROM post_targets other WHERE other.group_id=post_targets.group_id
         AND other.account_id=post_targets.account_id AND other.id!=post_targets.id
         AND (other.status IN ('published','publishing','outcome_unknown') OR
              (other.id<post_targets.id AND other.status!='cancelled')))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_active_group_account ON post_targets(group_id,account_id)
       WHERE status IN ('pending','waiting_network','publishing')""",
    """UPDATE post_targets SET status='outcome_unknown', error_code='outcome_unknown',
        error='محاولة قديمة لم تتأكد نتيجتها؛ افحص الحساب قبل إعادة الإرسال'
        WHERE status='waiting_network' AND COALESCE(error_code,'') NOT IN ('rate_limited','local_rate')""",
    """UPDATE reply_log SET
        public_status=CASE WHEN public_status='failed' THEN 'outcome_unknown' ELSE public_status END,
        dm_status=CASE WHEN dm_status='failed' THEN 'outcome_unknown' ELSE dm_status END
        WHERE public_status='failed' OR dm_status='failed'""",
    "ALTER TABLE reply_rules ADD COLUMN channel TEXT DEFAULT 'comment'",
    "ALTER TABLE reply_rules ADD COLUMN reaction TEXT DEFAULT ''",
    "ALTER TABLE reply_log ADD COLUMN reaction TEXT DEFAULT ''",
    "ALTER TABLE reply_log ADD COLUMN kind TEXT DEFAULT 'comment'",
    "ALTER TABLE reply_log ADD COLUMN held_reason TEXT DEFAULT ''",
]


def _migrate(c):
    ver = c.execute("PRAGMA user_version").fetchone()[0]
    for i in range(ver, len(MIGRATIONS)):
        try:
            c.execute(MIGRATIONS[i])
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise
        c.execute(f"PRAGMA user_version={i + 1}")


def schema_version():
    with _conn() as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def update_account_credentials(account_id, credentials):
    from core import accounts as acc_store
    with _conn() as c:
        c.execute("UPDATE accounts SET credentials=? WHERE id=?", (acc_store.encrypt(credentials), account_id))


def set_account_type(account_id, account_type):
    with _conn() as c:
        c.execute("UPDATE accounts SET account_type=? WHERE id=?", (account_type, account_id))


# ---------- صحة الحسابات وسجل التأخير ----------
def set_health(account_id, info):
    with _conn() as c:
        c.execute(
            "INSERT INTO account_health(account_id, ok, code, message, hint, detail, checked_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(account_id) DO UPDATE SET ok=excluded.ok, code=excluded.code, message=excluded.message, "
            "hint=excluded.hint, detail=excluded.detail, checked_at=excluded.checked_at",
            (account_id, 1 if info.get("ok") else 0, info.get("code", ""), info.get("message", ""),
             info.get("hint", ""), str(info.get("detail", ""))[:300], time.time()),
        )


def list_health():
    with _conn() as c:
        return {r["account_id"]: dict(r) for r in c.execute("SELECT * FROM account_health")}


def record_delay(target_id, platform, due_at, done_at=None):
    with _conn() as c:
        c.execute("INSERT INTO publish_delays(target_id, platform, due_at, done_at) VALUES(?,?,?,?)",
                  (target_id, platform, due_at, done_at or time.time()))
        c.execute("DELETE FROM publish_delays WHERE id NOT IN (SELECT id FROM publish_delays ORDER BY id DESC LIMIT 200)")


def recent_delays(limit=30):
    with _conn() as c:
        rows = c.execute("SELECT * FROM publish_delays ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"platform": r["platform"], "due_at": r["due_at"], "delay": max(0.0, r["done_at"] - r["due_at"])} for r in reversed(rows)]


def overdue_count(now=None):
    now = time.time() if now is None else now
    with _conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM post_targets t JOIN post_groups g ON g.id=t.group_id "
            "WHERE g.status='scheduled' AND t.status IN ('pending','waiting_network') AND t.due_at<=? AND t.next_try_at<=?",
            (now - 60, now)).fetchone()[0]


# ---------- الحسابات ----------
def add_account(platform, username, credentials_json, account_type="personal", active=True):
    from core import accounts as acc_store
    with _conn() as c:
        c.execute(
            "INSERT INTO accounts(platform, username, credentials, account_type, created_at, active) VALUES(?,?,?,?,?,?)",
            (platform, username, acc_store.encrypt(credentials_json), account_type, time.time(), 1 if active else 0),
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
    if action not in ("comments", "likes", "replies", "dms") or max_per_hour <= 0:
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


def claim_legacy_posts():
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute("SELECT * FROM scheduled_posts WHERE status='pending' AND scheduled_at<=?", (time.time(),)).fetchall()
        for row in rows:
            c.execute("UPDATE scheduled_posts SET status='publishing' WHERE id=? AND status='pending'", (row["id"],))
        return rows


def recover_legacy_posts():
    with _conn() as c:
        c.execute("UPDATE scheduled_posts SET status='outcome_unknown', error='توقف التطبيق بعد بدء الإرسال؛ افحص الحساب قبل إنشاء محاولة جديدة' WHERE status='publishing'")


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


def set_secret(name, value):
    """يخزّن مفتاحاً حساساً مشفّراً (Oxion, Gemini, GitHub...). القيمة الفارغة تحذفه."""
    from core import accounts as acc_store
    if not value:
        with _conn() as c:
            c.execute("DELETE FROM settings WHERE key=?", (name,))
        return
    set_setting(name, acc_store.encrypt({"value": value}))


def get_secret(name, default=""):
    from core import accounts as acc_store
    stored = get_setting(name, "")
    if not stored:
        return default
    try:
        return acc_store.decrypt(stored).get("value", default)
    except Exception:
        return default


def set_api_key(value):
    """مفتاح Oxion (المزوّد الأساسي للذكاء)."""
    set_secret("oxion_api_key", value)


def get_api_key(default=""):
    return get_secret("oxion_api_key", default)


# ---------- مجموعات النشر (منشور واحد لعدة منصات) ----------
_ACTIVE_TARGET = ("pending", "waiting_network", "publishing")


def _request_digest(payload):
    import hashlib
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def replay_group_request(request_key, payload):
    with _conn() as c:
        row = c.execute("SELECT id,request_digest FROM post_groups WHERE request_key=?", (request_key,)).fetchone()
        if row:
            if row["request_digest"] != _request_digest(payload):
                raise ValueError("معرّف الطلب مستخدم لمحتوى مختلف؛ أعد فتح المحرر")
            return row["id"]
    return None


def create_group(text, media_id, media_kind, scheduled_at, targets, status="scheduled", source="manual", meta=None, request_key=None, request_payload=None):
    """targets: [{account_id, platform, text_override}]. يعيد معرّف المجموعة."""
    unique = {}
    for target in targets:
        aid = int(target["account_id"])
        if aid in unique and unique[aid] != target:
            raise ValueError("نصان مختلفان للحساب نفسه في المنشور")
        unique[aid] = target
    targets = list(unique.values())
    payload = request_payload if request_payload is not None else [text, media_id, media_kind, scheduled_at, targets, status, source, meta or {}]
    digest = _request_digest(payload)
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        if request_key:
            existing = c.execute("SELECT id,request_digest FROM post_groups WHERE request_key=?", (request_key,)).fetchone()
            if existing:
                if existing["request_digest"] != digest:
                    raise ValueError("معرّف الطلب مستخدم لمحتوى مختلف؛ أعد فتح المحرر")
                return existing["id"]
        cur = c.execute(
            "INSERT INTO post_groups(text, media_id, media_kind, scheduled_at, status, source, meta, created_at,request_key,request_digest) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (text, media_id or "", media_kind or "", scheduled_at, status, source,
             json.dumps(meta or {}, ensure_ascii=False), time.time(), request_key, digest),
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
            "SELECT 1 FROM post_targets WHERE group_id=? AND status IN ('publishing','published','outcome_unknown')", (group_id,)
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
            "UPDATE post_targets SET status='cancelled' WHERE group_id=? AND status IN ('pending','waiting_network','missed','failed','needs_you')",
            (group_id,),
        )
        still = c.execute(
            "SELECT 1 FROM post_targets WHERE group_id=? AND status IN ('publishing','published','outcome_unknown')", (group_id,)
        ).fetchone()
        if not still:
            c.execute("UPDATE post_groups SET status='cancelled' WHERE id=?", (group_id,))
        media = g["media_id"] if not still else ""
        return True, media


def retry_target(target_id, confirm_unknown=False):
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        now = time.time()
        row = c.execute("SELECT * FROM post_targets WHERE id=?", (target_id,)).fetchone()
        if not row or row["status"] not in ("failed", "missed", "outcome_unknown"):
            return False
        if row["status"] == "outcome_unknown" and not confirm_unknown:
            return False
        duplicate = c.execute("SELECT 1 FROM post_targets WHERE group_id=? AND account_id=? AND id!=? AND status!='cancelled'",
                              (row["group_id"],row["account_id"],target_id)).fetchone()
        if duplicate:
            return False
        cur = c.execute(
            "UPDATE post_targets SET status='pending', attempts=0, due_at=?, next_try_at=0, error='', error_code='', updated_at=? WHERE id=?",
            (now, now, target_id),
        )
        return cur.rowcount == 1


def due_platforms(now=None):
    now = time.time() if now is None else now
    with _conn() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT t.platform FROM post_targets t JOIN post_groups g ON g.id=t.group_id "
            "WHERE g.status='scheduled' AND t.status IN ('pending','waiting_network') AND t.due_at<=? AND t.next_try_at<=?",
            (now, now))]


def claim_due_targets(now=None, grace_sec=12 * 3600, platform=None, limit=None):
    """يحجز الأهداف المستحقة ذرّياً (publishing) قبل الإرسال. المتأخر عن المهلة يصير missed."""
    now = time.time() if now is None else now
    q = ("SELECT t.* FROM post_targets t JOIN post_groups g ON g.id=t.group_id "
         "WHERE g.status='scheduled' AND t.status IN ('pending','waiting_network') "
         "AND t.due_at<=? AND t.next_try_at<=?")
    args = [now, now]
    if platform:
        q += " AND t.platform=?"
        args.append(platform)
    q += " ORDER BY t.due_at, t.id"
    if limit:
        q += f" LIMIT {int(limit)}"
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute(q, args).fetchall()
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


def recover_stuck_targets(now=None, older_than=900, busy_platforms=()):
    """هدف بقي publishing بعد قتل العملية: لا نعيد الإرسال أبداً لأن النتيجة غير معروفة.

    المنصات التي يعمل عاملها الآن مستثناة، فرفع فيديو طويل لا يُعدّ عالقاً."""
    now = time.time() if now is None else now
    q = ("UPDATE post_targets SET status='outcome_unknown', error='توقفت العملية أثناء النشر، تحقق من الحساب قبل إعادة المحاولة', "
         "error_code='outcome_unknown', updated_at=? WHERE status='publishing' AND updated_at<?")
    args = [now, now - older_than]
    busy = list(busy_platforms)
    if busy:
        q += f" AND platform NOT IN ({','.join('?' * len(busy))})"
        args += busy
    with _conn() as c:
        return c.execute(q, args).rowcount


def finish_target(target_id, status, error="", remote_id="", next_try_at=0, error_code=""):
    with _conn() as c:
        c.execute(
            "UPDATE post_targets SET status=?, error=?, remote_id=?, next_try_at=?, error_code=?, updated_at=? WHERE id=?",
            (status, error[:500], remote_id, next_try_at, error_code, time.time(), target_id),
        )


def get_target(target_id):
    with _conn() as c:
        return c.execute("SELECT * FROM post_targets WHERE id=?", (target_id,)).fetchone()


def confirm_manual_target(target_id, confirmed_published=False):
    """تأكيد أنك نشرت يدوياً منشوراً ينتظرك (مشاركة نصف آلية)."""
    with _conn() as c:
        return c.execute(
            "UPDATE post_targets SET status='published', error='', error_code='', updated_at=? WHERE id=? AND (status='needs_you' OR (status='outcome_unknown' AND ?))",
            (time.time(), target_id, confirmed_published)).rowcount == 1


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
        for r in c.execute("SELECT media_path FROM scheduled_posts WHERE status IN ('pending','publishing','outcome_unknown','failed') AND media_path IS NOT NULL"):
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


def add_rule(name, kind, topic, account_ids, times, tone, require_approval, active=True):
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO auto_rules(name, kind, topic, account_ids, times, tone, require_approval, created_at, active) VALUES(?,?,?,?,?,?,?,?,?)",
            (name, kind, topic, json.dumps(account_ids), json.dumps(times), tone, 1 if require_approval else 0, time.time(), 1 if active else 0),
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


# ---------- الردود التلقائية على التعليقات ----------
def add_reply_rule(account_id, name, media_id, keywords, any_comment, whole_word, public_replies, dm_text, dm_link, active=True,
                   channel="comment", reaction=""):
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO reply_rules(account_id, name, media_id, keywords, any_comment, whole_word, public_replies, dm_text, dm_link, created_at, active, channel, reaction) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (account_id, name, media_id, json.dumps(keywords, ensure_ascii=False), 1 if any_comment else 0,
             1 if whole_word else 0, json.dumps(public_replies, ensure_ascii=False), dm_text, dm_link, time.time(), 1 if active else 0, channel, reaction),
        )
        return cur.lastrowid


def list_reply_rules(account_id=None, active_only=False):
    q, args = "SELECT * FROM reply_rules WHERE 1=1", []
    if account_id is not None:
        q += " AND account_id=?"
        args.append(account_id)
    if active_only:
        q += " AND active=1"
    with _conn() as c:
        return c.execute(q + " ORDER BY (media_id='') , id", args).fetchall()


def update_reply_rule(rule_id, **fields):
    json_fields = {"keywords", "public_replies"}
    flags = {"any_comment", "whole_word", "active"}
    allowed = json_fields | flags | {"name", "media_id", "dm_text", "dm_link", "channel", "reaction"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in json_fields:
            v = json.dumps(v, ensure_ascii=False)
        elif k in flags:
            v = 1 if v else 0
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return False
    with _conn() as c:
        cur = c.execute(f"UPDATE reply_rules SET {', '.join(sets)} WHERE id=?", (*args, rule_id))
        if "active" in fields and not fields["active"]:
            _cancel_rule_replies(c, rule_id)
        return cur.rowcount == 1


def _cancel_rule_replies(c, rule_id):
    c.execute("""UPDATE reply_log SET
        public_status=CASE WHEN public_status='pending' THEN 'skipped' ELSE public_status END,
        dm_status=CASE WHEN dm_status='pending' THEN 'skipped' ELSE dm_status END,
        error='أوقف المستخدم القاعدة؛ أُلغيت الأجزاء التي لم يبدأ إرسالها', updated_at=?
        WHERE rule_id=? AND status='queued'""", (time.time(),rule_id))
    c.execute("UPDATE reply_log SET status='done' WHERE rule_id=? AND public_status NOT IN ('pending','sending') AND dm_status NOT IN ('pending','sending')", (rule_id,))


def delete_reply_rule(rule_id):
    with _conn() as c:
        _cancel_rule_replies(c, rule_id)
        c.execute("DELETE FROM reply_rules WHERE id=?", (rule_id,))


def claim_reply_part(reply_id, part):
    if part not in ("public", "dm"):
        raise ValueError("جزء رد غير صالح")
    col = "public_status" if part == "public" else "dm_status"
    with _conn() as c:
        cur = c.execute(f"""UPDATE reply_log SET {col}='sending', updated_at=?
            WHERE id=? AND status='queued' AND {col}='pending'
            AND EXISTS(SELECT 1 FROM reply_rules r WHERE r.id=reply_log.rule_id AND r.active=1)
            AND EXISTS(SELECT 1 FROM accounts a WHERE a.id=reply_log.account_id AND a.active=1)""",
                        (time.time(),reply_id))
        return cur.rowcount == 1


def defer_reply_part(reply_id, part, error="", delay=300):
    """لا تسمح مهلة المعدل بإحياء رد أوقفه المستخدم أثناء العمل."""
    if part not in ("public", "dm"):
        raise ValueError("جزء رد غير صالح")
    col = "public_status" if part == "public" else "dm_status"
    now = time.time()
    with _conn() as c:
        c.execute(f"""UPDATE reply_log SET {col}=CASE WHEN EXISTS(
            SELECT 1 FROM reply_rules r WHERE r.id=reply_log.rule_id AND r.active=1)
            THEN 'pending' ELSE 'skipped' END, release_at=?, error=?, updated_at=?
            WHERE id=? AND status='queued' AND {col} IN ('pending','sending')""",
                  (now+delay, error[:300],now,reply_id))
        c.execute("UPDATE reply_log SET status='done' WHERE id=? AND public_status NOT IN ('pending','sending') AND dm_status NOT IN ('pending','sending')", (reply_id,))


def queue_reply(rule_id, account_id, comment, public_text, dm_text, release_at, status="queued", reaction="", held_reason=""):
    """يسجّل تعليقاً للرد. UNIQUE يمنع الرد مرتين على نفس التعليق. يعيد None إن سبق."""
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO reply_log(rule_id, account_id, comment_id, media_id, user_id, username, comment_text, "
            "public_text, dm_text, public_status, dm_status, release_at, created_at, updated_at, status, kind, reaction, held_reason) SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,? "
            "WHERE EXISTS(SELECT 1 FROM reply_rules WHERE id=? AND active=1) "
            "AND EXISTS(SELECT 1 FROM accounts WHERE id=? AND active=1)",
            (rule_id, account_id, comment["comment_id"], comment["media_id"], comment["user_id"], comment["username"],
             comment["text"], public_text, dm_text, "pending" if public_text else "none", "pending" if (dm_text or reaction) else "none",
             release_at, now, now, status, comment.get("kind", "comment"), reaction, held_reason, rule_id, account_id),
        )
        return cur.lastrowid if cur.rowcount else None


def user_replied_on_media(account_id, media_id, user_id):
    """true إن سبق تسجيل رد (حتى المسودة) لنفس الشخص على نفس المنشور: رسالة واحدة لكل شخص."""
    if not user_id:
        return False
    with _conn() as c:
        return c.execute("SELECT 1 FROM reply_log WHERE account_id=? AND media_id=? AND user_id=? AND status!='rejected'",
                         (account_id, media_id, user_id)).fetchone() is not None


def recent_reply_to_user(account_id, user_id, kind, since):
    """true إن سبق تسجيل رد (غير مرفوض) لهذا الشخص من النوع نفسه بعد الوقت since."""
    if not user_id:
        return False
    with _conn() as c:
        return c.execute("SELECT 1 FROM reply_log WHERE account_id=? AND user_id=? AND kind=? AND created_at>=? "
                         "AND status!='rejected'", (account_id, user_id, kind, since)).fetchone() is not None


def sent_since(since):
    """عدد الردود التي خرجت فعلاً (علنية أو خاصة) بعد الوقت since: للسقف اليومي."""
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM reply_log WHERE (public_status='sent' OR dm_status='sent') AND updated_at>=?",
                         (since,)).fetchone()[0]


def reply_counts():
    with _conn() as c:
        return {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM reply_log GROUP BY status")}


def decide_draft(reply_id, approve):
    """يعتمد مسودة (تصير في الطابور) أو يرفضها. لا يعتمد إن كانت القاعدة أو الحساب متوقفين."""
    with _conn() as c:
        if approve:
            cur = c.execute(
                "UPDATE reply_log SET status='queued', release_at=?, updated_at=? WHERE id=? AND status='draft' "
                "AND EXISTS(SELECT 1 FROM reply_rules r WHERE r.id=reply_log.rule_id AND r.active=1) "
                "AND EXISTS(SELECT 1 FROM accounts a WHERE a.id=reply_log.account_id AND a.active=1)",
                (time.time(), time.time(), reply_id))
        else:
            cur = c.execute("UPDATE reply_log SET status='rejected', updated_at=? WHERE id=? AND status='draft'",
                            (time.time(), reply_id))
        return cur.rowcount == 1


def seen_comment(account_id, comment_id):
    with _conn() as c:
        return c.execute("SELECT 1 FROM reply_log WHERE account_id=? AND comment_id=?", (account_id, comment_id)).fetchone() is not None


def due_replies(now=None):
    now = time.time() if now is None else now
    with _conn() as c:
        return c.execute("SELECT * FROM reply_log WHERE status='queued' AND release_at<=? ORDER BY release_at, id", (now,)).fetchall()


def set_reply_part(reply_id, part, state, error="", release_at=None, attempts_inc=False):
    """part: public أو dm. state: pending/sending/sent/failed. 'sending' يُسجَّل قبل الإرسال."""
    col = "public_status" if part == "public" else "dm_status"
    with _conn() as c:
        c.execute(f"UPDATE reply_log SET {col}=?, error=?, updated_at=? WHERE id=?", (state, error[:300], time.time(), reply_id))
        if release_at is not None:
            c.execute("UPDATE reply_log SET release_at=? WHERE id=?", (release_at, reply_id))
        if attempts_inc:
            c.execute("UPDATE reply_log SET attempts=attempts+1 WHERE id=?", (reply_id,))
        row = c.execute("SELECT public_status, dm_status FROM reply_log WHERE id=?", (reply_id,)).fetchone()
        if row["public_status"] not in ("pending", "sending") and row["dm_status"] not in ("pending", "sending"):
            c.execute("UPDATE reply_log SET status='done' WHERE id=?", (reply_id,))


def recover_stuck_replies(now=None, older_than=900):
    now = time.time() if now is None else now
    with _conn() as c:
        for col in ("public_status", "dm_status"):
            c.execute(f"UPDATE reply_log SET {col}='outcome_unknown', error='توقفت العملية أثناء الإرسال، النتيجة غير مؤكدة؛ افحص الحساب قبل إعادة المحاولة', updated_at=? "
                      f"WHERE {col}='sending' AND updated_at<?", (now, now - older_than))
        c.execute("UPDATE reply_log SET status='done' WHERE status='queued' AND public_status NOT IN ('pending','sending') "
                  "AND dm_status NOT IN ('pending','sending')")


def list_reply_log(limit=100):
    with _conn() as c:
        return c.execute("SELECT * FROM reply_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
