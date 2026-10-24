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
        """)


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
        exists = c.execute("SELECT 1 FROM comment_queue WHERE post_id=?", (post_id,)).fetchone()
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


def approve_comment(comment_id):
    """الموافقة تضيف تأخيراً عشوائياً واقعياً قبل النشر الفعلي."""
    limits_min = 180
    limits_max = 3600
    with _conn() as c:
        row = c.execute("SELECT platform FROM comment_queue WHERE id=?", (comment_id,)).fetchone()
        if row and row["platform"] in config.RATE_LIMITS:
            limits_min = config.RATE_LIMITS[row["platform"]]["min_delay_sec"]
            limits_max = config.RATE_LIMITS[row["platform"]]["max_delay_sec"]
        c.execute(
            "UPDATE comment_queue SET status='approved', release_at=? WHERE id=?",
            (time.time() + random.uniform(limits_min, limits_max), comment_id),
        )


def reject_comment(comment_id):
    with _conn() as c:
        c.execute("UPDATE comment_queue SET status='rejected' WHERE id=?", (comment_id,))


def approved_due():
    with _conn() as c:
        return c.execute(
            "SELECT * FROM comment_queue WHERE status='approved' AND release_at<=?", (time.time(),)
        ).fetchall()


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
