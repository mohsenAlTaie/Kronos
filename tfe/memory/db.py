from __future__ import annotations

import asyncio
import os
import re
import shutil
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..logs import MEMORY as log

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role    TEXT    NOT NULL,
    content TEXT    NOT NULL,
    ts      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_ts   ON messages(ts);
CREATE TABLE IF NOT EXISTS facts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    fact    TEXT    NOT NULL,
    norm    TEXT    NOT NULL,
    created REAL    NOT NULL,
    expires REAL    NOT NULL,
    hits    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, norm)
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id, expires);
CREATE TABLE IF NOT EXISTS summaries (
    user_id INTEGER PRIMARY KEY,
    summary TEXT NOT NULL,
    updated REAL NOT NULL,
    covered_msg_id INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS warnings (
    user_id INTEGER PRIMARY KEY,
    count   INTEGER NOT NULL,
    last_ts REAL    NOT NULL,
    reason  TEXT
);
CREATE TABLE IF NOT EXISTS blocked (
    user_id    INTEGER PRIMARY KEY,
    reason     TEXT,
    label      TEXT,
    blocked_at REAL NOT NULL,
    tg_blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mod_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    ts         REAL NOT NULL,
    label      TEXT,
    confidence REAL,
    source     TEXT,
    action     TEXT
);
CREATE INDEX IF NOT EXISTS idx_modlog_user ON mod_log(user_id, ts);
CREATE TABLE IF NOT EXISTS trusted_users (
    user_id  INTEGER PRIMARY KEY,
    tier     TEXT NOT NULL DEFAULT 'vip',
    label    TEXT NOT NULL DEFAULT '',
    added_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id    INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL DEFAULT '',
    last_name  TEXT NOT NULL DEFAULT '',
    username   TEXT NOT NULL DEFAULT '',
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL,
    msg_count  INTEGER NOT NULL DEFAULT 0,
    lang       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mod_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    ts         REAL NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'text',
    label      TEXT,
    confidence REAL,
    source     TEXT,
    action     TEXT NOT NULL,
    reason     TEXT,
    evidence   TEXT,
    strike     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_modevents_user ON mod_events(user_id, id);
CREATE TABLE IF NOT EXISTS learned_terms (
    term     TEXT PRIMARY KEY,
    label    TEXT NOT NULL,
    hits     INTEGER NOT NULL DEFAULT 1,
    first_ts REAL NOT NULL,
    last_ts  REAL NOT NULL,
    source   TEXT NOT NULL DEFAULT 'llm',
    status   TEXT NOT NULL DEFAULT 'pending'
);
"""


@dataclass
class AbuseOutcome:
    action: str
    count: int
    newly_blocked: bool


def _norm_fact(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())[:300]


class Database:
    def __init__(self, path: str):
        self.path = path
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tfe-db")
        self._conn: Optional[sqlite3.Connection] = None
        self._closed = False

    async def _run(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        if self._closed:
            raise RuntimeError("database closed")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._ex, self._call, fn)

    def _call(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        assert self._conn is not None, "Database.open() not awaited"
        return fn(self._conn)

    async def open(self) -> None:
        def _open() -> None:
            d = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(d, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                   check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._conn = conn
            self._migrate(conn)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._ex, _open)

    async def close(self) -> None:
        if self._closed:
            return
        def _close() -> None:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                self._conn.close()
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._ex, _close)
        finally:
            self._closed = True
            self._ex.shutdown(wait=False)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        legacy = {"history", "user_summaries", "blocked_users"} & tables
        conn.executescript(_SCHEMA)
        row = conn.execute("SELECT v FROM meta WHERE k='legacy_imported'").fetchone()
        if legacy and not row:
            self._import_legacy(conn, tables)
        conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))

    @staticmethod
    def _to_epoch(v: Any) -> float:
        if v is None:
            return time.time()
        if isinstance(v, (int, float)):
            return float(v)
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(str(v)).timestamp()
        except Exception:
            return time.time()

    def _import_legacy(self, conn: sqlite3.Connection, tables: set) -> None:
        n = 0
        try:
            conn.execute("BEGIN IMMEDIATE")
            if "history" in tables:
                for r in conn.execute("SELECT user_id, role, content, ts FROM history ORDER BY id").fetchall():
                    if r["content"]:
                        conn.execute("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)",
                                     (r["user_id"], r["role"], r["content"], self._to_epoch(r["ts"])))
                        n += 1
            if "user_summaries" in tables:
                for r in conn.execute("SELECT user_id, summary, updated_at FROM user_summaries").fetchall():
                    if r["summary"]:
                        conn.execute("INSERT OR IGNORE INTO summaries(user_id,summary,updated) VALUES(?,?,?)",
                                     (r["user_id"], r["summary"], self._to_epoch(r["updated_at"])))
            if "blocked_users" in tables:
                cols = {c[1] for c in conn.execute("PRAGMA table_info(blocked_users)")}
                for r in conn.execute("SELECT * FROM blocked_users").fetchall():
                    permanent = ("permanent" in cols and r["permanent"]) or ("block_until" in cols and not r["block_until"])
                    if permanent:
                        conn.execute("INSERT OR IGNORE INTO blocked(user_id,reason,label,blocked_at,tg_blocked) "
                                     "VALUES(?,?,?,?,1)",
                                     (r["user_id"], r["reason"] if "reason" in cols else "legacy", "LEGACY", time.time()))
            conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('legacy_imported','1')")
            conn.execute("COMMIT")
            log.info(f"legacy database imported ({n} messages)")
        except Exception as e:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            log.warning(f"legacy import skipped: {type(e).__name__}")

    async def add_message(self, user_id: int, role: str, content: str) -> int:
        def f(c: sqlite3.Connection) -> int:
            cur = c.execute("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)",
                            (user_id, role, content, time.time()))
            return int(cur.lastrowid)
        return await self._run(f)

    async def recent_messages(self, user_id: int, limit: int, max_age_days: Optional[float] = None) -> List[Dict[str, Any]]:
        def f(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            q = "SELECT id, role, content, ts FROM messages WHERE user_id=?"
            args: List[Any] = [user_id]
            if max_age_days:
                q += " AND ts>=?"
                args.append(time.time() - max_age_days * 86400)
            q += " ORDER BY id DESC LIMIT ?"
            args.append(limit)
            rows = c.execute(q, args).fetchall()
            return [dict(r) for r in reversed(rows)]
        return await self._run(f)

    async def message_count(self, user_id: int) -> int:
        return await self._run(lambda c: c.execute("SELECT COUNT(*) FROM messages WHERE user_id=?", (user_id,)).fetchone()[0])

    async def messages_after(self, user_id: int, after_id: int, limit: int = 60) -> List[Dict[str, Any]]:
        def f(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            rows = c.execute("SELECT id, role, content FROM messages WHERE user_id=? AND id>? ORDER BY id LIMIT ?",
                             (user_id, after_id, limit)).fetchall()
            return [dict(r) for r in rows]
        return await self._run(f)

    async def prune_messages(self, user_id: int, keep_last: int) -> int:
        def f(c: sqlite3.Connection) -> int:
            cur = c.execute("DELETE FROM messages WHERE user_id=? AND id NOT IN "
                            "(SELECT id FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?)",
                            (user_id, user_id, keep_last))
            return cur.rowcount
        return await self._run(f)

    async def add_fact(self, user_id: int, fact: str, ttl_days: float) -> bool:
        fact = (fact or "").strip()[:300]
        if len(fact) < 4:
            return False
        norm = _norm_fact(fact)
        now = time.time()
        def f(c: sqlite3.Connection) -> bool:
            cur = c.execute("INSERT OR IGNORE INTO facts(user_id,fact,norm,created,expires) VALUES(?,?,?,?,?)",
                            (user_id, fact, norm, now, now + ttl_days * 86400))
            if cur.rowcount == 0:
                c.execute("UPDATE facts SET expires=?, fact=? WHERE user_id=? AND norm=?",
                          (now + ttl_days * 86400, fact, user_id, norm))
                return False
            c.execute("DELETE FROM facts WHERE user_id=? AND id NOT IN "
                      "(SELECT id FROM facts WHERE user_id=? ORDER BY created DESC LIMIT 60)", (user_id, user_id))
            return True
        return await self._run(f)

    async def get_facts(self, user_id: int) -> List[Dict[str, Any]]:
        def f(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            rows = c.execute("SELECT id, fact, created, hits FROM facts WHERE user_id=? AND expires>? ORDER BY created DESC",
                             (user_id, time.time())).fetchall()
            return [dict(r) for r in rows]
        return await self._run(f)

    async def bump_facts(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        def f(c: sqlite3.Connection) -> None:
            c.executemany("UPDATE facts SET hits=hits+1 WHERE id=?", [(i,) for i in ids])
        await self._run(f)

    async def get_summary(self, user_id: int) -> Tuple[str, int]:
        def f(c: sqlite3.Connection) -> Tuple[str, int]:
            r = c.execute("SELECT summary, covered_msg_id FROM summaries WHERE user_id=?", (user_id,)).fetchone()
            return (r["summary"], r["covered_msg_id"]) if r else ("", 0)
        return await self._run(f)

    async def set_summary(self, user_id: int, summary: str, covered_msg_id: int) -> None:
        def f(c: sqlite3.Connection) -> None:
            c.execute("INSERT INTO summaries(user_id,summary,updated,covered_msg_id) VALUES(?,?,?,?) "
                      "ON CONFLICT(user_id) DO UPDATE SET summary=excluded.summary, updated=excluded.updated, "
                      "covered_msg_id=excluded.covered_msg_id", (user_id, summary, time.time(), covered_msg_id))
        await self._run(f)

    async def is_blocked(self, user_id: int) -> bool:
        return await self._run(lambda c: c.execute("SELECT 1 FROM blocked WHERE user_id=?", (user_id,)).fetchone() is not None)

    async def blocked_ids(self) -> List[int]:
        return await self._run(lambda c: [r[0] for r in c.execute("SELECT user_id FROM blocked")])

    async def get_warning_count(self, user_id: int, ttl_hours: float) -> int:
        def f(c: sqlite3.Connection) -> int:
            r = c.execute("SELECT count, last_ts FROM warnings WHERE user_id=?", (user_id,)).fetchone()
            if not r or time.time() - r["last_ts"] > ttl_hours * 3600:
                return 0
            return int(r["count"])
        return await self._run(f)

    async def register_abuse(self, user_id: int, *, max_warnings: int, ttl_hours: float,
                             reason: str = "", label: str = "", kind: str = "text", evidence: str = "",
                             confidence: Optional[float] = None, source: str = "",
                             instant: bool = False) -> AbuseOutcome:
        def f(c: sqlite3.Connection) -> AbuseOutcome:
            c.execute("BEGIN IMMEDIATE")
            try:
                if c.execute("SELECT 1 FROM blocked WHERE user_id=?", (user_id,)).fetchone():
                    c.execute("COMMIT")
                    return AbuseOutcome("already_blocked", max_warnings, False)
                now = time.time()
                r = c.execute("SELECT count, last_ts FROM warnings WHERE user_id=?", (user_id,)).fetchone()
                prev = int(r["count"]) if r and now - r["last_ts"] <= ttl_hours * 3600 else 0
                new = max(prev + 1, max_warnings) if instant else prev + 1
                if new >= max_warnings:
                    c.execute("INSERT OR REPLACE INTO blocked(user_id,reason,label,blocked_at,tg_blocked) VALUES(?,?,?,?,0)",
                              (user_id, reason[:200], label, now))
                    c.execute("DELETE FROM warnings WHERE user_id=?", (user_id,))
                    out = AbuseOutcome("block", new, True)
                else:
                    c.execute("INSERT INTO warnings(user_id,count,last_ts,reason) VALUES(?,?,?,?) "
                              "ON CONFLICT(user_id) DO UPDATE SET count=excluded.count, last_ts=excluded.last_ts, "
                              "reason=excluded.reason", (user_id, new, now, reason[:200]))
                    out = AbuseOutcome("warn", new, False)
                c.execute("INSERT INTO mod_log(user_id,ts,label,confidence,source,action) VALUES(?,?,?,?,?,?)",
                          (user_id, now, label, None, reason[:60], out.action))
                c.execute("INSERT INTO mod_events(user_id,ts,kind,label,confidence,source,action,reason,evidence,strike) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (user_id, now, kind, label, confidence, (source or "")[:40], out.action, reason[:200],
                           (evidence or "")[:400], new))
                c.execute("COMMIT")
                return out
            except BaseException:
                try:
                    c.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        return await self._run(f)

    async def mark_tg_blocked(self, user_id: int) -> None:
        await self._run(lambda c: c.execute("UPDATE blocked SET tg_blocked=1 WHERE user_id=?", (user_id,)))

    async def block(self, user_id: int, reason: str = "manual", label: str = "MANUAL") -> None:
        def f(c: sqlite3.Connection) -> None:
            c.execute("INSERT OR REPLACE INTO blocked(user_id,reason,label,blocked_at,tg_blocked) VALUES(?,?,?,?,0)",
                      (user_id, reason[:200], label, time.time()))
            c.execute("DELETE FROM warnings WHERE user_id=?", (user_id,))
        await self._run(f)

    async def unblock(self, user_id: int) -> bool:
        def f(c: sqlite3.Connection) -> bool:
            a = c.execute("DELETE FROM blocked WHERE user_id=?", (user_id,)).rowcount
            b = c.execute("DELETE FROM warnings WHERE user_id=?", (user_id,)).rowcount
            return bool(a or b)
        return await self._run(f)

    async def trust_add(self, user_id: int, tier: str = "vip", label: str = "") -> bool:
        def f(c: sqlite3.Connection) -> bool:
            new = c.execute("SELECT 1 FROM trusted_users WHERE user_id=?", (user_id,)).fetchone() is None
            c.execute("INSERT OR REPLACE INTO trusted_users(user_id,tier,label,added_ts) VALUES(?,?,?,?)",
                      (user_id, tier, (label or "")[:80], time.time()))
            return new
        return await self._run(f)

    async def trust_remove(self, user_id: int) -> bool:
        return await self._run(lambda c: bool(c.execute("DELETE FROM trusted_users WHERE user_id=?", (user_id,)).rowcount))

    async def trust_list(self) -> List[Dict[str, Any]]:
        def f(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            return [dict(r) for r in c.execute("SELECT user_id,tier,label,added_ts FROM trusted_users ORDER BY added_ts")]
        return await self._run(f)

    async def learn_term(self, term: str, label: str, source: str = "llm", promote_at: int = 2) -> Tuple[int, str]:
        def f(c: sqlite3.Connection) -> Tuple[int, str]:
            now = time.time()
            row = c.execute("SELECT hits,status FROM learned_terms WHERE term=?", (term,)).fetchone()
            if row is None:
                status = "active" if source == "owner" or promote_at <= 1 else "pending"
                c.execute("INSERT INTO learned_terms(term,label,hits,first_ts,last_ts,source,status) VALUES(?,?,?,?,?,?,?)",
                          (term, label, 1, now, now, source, status))
                return 1, status
            hits, status = row["hits"], row["status"]
            if status == "rejected" and source != "owner":
                return hits, status
            hits += 1
            if source == "owner" or hits >= promote_at:
                status = "active"
            c.execute("UPDATE learned_terms SET hits=?,last_ts=?,status=?,label=? WHERE term=?",
                      (hits, now, status, label, term))
            return hits, status
        return await self._run(f)

    async def active_terms(self) -> Dict[str, str]:
        return await self._run(lambda c: {r["term"]: r["label"] for r in
                                          c.execute("SELECT term,label FROM learned_terms WHERE status='active'")})

    async def list_terms(self, limit: int = 40) -> List[Dict[str, Any]]:
        def f(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            return [dict(r) for r in c.execute(
                "SELECT term,label,hits,status,source FROM learned_terms ORDER BY status='active' DESC, last_ts DESC LIMIT ?",
                (limit,))]
        return await self._run(f)

    async def set_term_status(self, term: str, status: str) -> bool:
        def f(c: sqlite3.Connection) -> bool:
            if c.execute("SELECT 1 FROM learned_terms WHERE term=?", (term,)).fetchone() is None:
                c.execute("INSERT INTO learned_terms(term,label,hits,first_ts,last_ts,source,status) VALUES(?,?,?,?,?,?,?)",
                          (term, "PROFANITY", 0, time.time(), time.time(), "owner", status))
                return True
            return bool(c.execute("UPDATE learned_terms SET status=?,last_ts=? WHERE term=?",
                                  (status, time.time(), term)).rowcount)
        return await self._run(f)

    async def list_warnings(self) -> List[Dict[str, Any]]:
        def f(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            return [dict(r) for r in c.execute("SELECT user_id,count,last_ts,reason FROM warnings ORDER BY last_ts DESC LIMIT 50")]
        return await self._run(f)

    async def list_blocked(self) -> List[Dict[str, Any]]:
        def f(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            return [dict(r) for r in c.execute("SELECT user_id,reason,label,blocked_at FROM blocked ORDER BY blocked_at DESC LIMIT 50")]
        return await self._run(f)

    async def touch_profile(self, user_id: int, first_name: str = "", last_name: str = "", username: str = "",
                            lang: str = "", count: int = 0) -> None:
        def f(c: sqlite3.Connection) -> None:
            now = time.time()
            c.execute(
                "INSERT INTO user_profiles(user_id,first_name,last_name,username,first_seen,last_seen,msg_count,lang) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "first_name=CASE WHEN excluded.first_name<>'' THEN excluded.first_name ELSE first_name END, "
                "last_name=CASE WHEN excluded.first_name<>'' THEN excluded.last_name ELSE last_name END, "
                "username=CASE WHEN excluded.first_name<>'' OR excluded.username<>'' THEN excluded.username ELSE username END, "
                "lang=CASE WHEN excluded.lang<>'' THEN excluded.lang ELSE lang END, "
                "last_seen=excluded.last_seen, msg_count=msg_count+?",
                (user_id, first_name[:80], last_name[:80], username[:40], now, now, count, lang[:12], count))
        await self._run(f)

    async def get_profile(self, user_id: int) -> Optional[Dict[str, Any]]:
        def f(c: sqlite3.Connection) -> Optional[Dict[str, Any]]:
            r = c.execute("SELECT * FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
            return dict(r) if r else None
        return await self._run(f)

    async def recent_profiles(self, limit: int = 8, offset: int = 0) -> List[Dict[str, Any]]:
        def f(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            return [dict(r) for r in c.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM messages m WHERE m.user_id=p.user_id) AS stored, "
                "(SELECT COUNT(*) FROM facts f WHERE f.user_id=p.user_id AND f.expires>?) AS facts "
                "FROM user_profiles p ORDER BY p.last_seen DESC LIMIT ? OFFSET ?", (time.time(), limit, offset))]
        return await self._run(f)

    async def profile_count(self) -> int:
        return await self._run(lambda c: c.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0])

    async def add_mod_event(self, user_id: int, action: str, *, kind: str = "manual", label: str = "",
                            reason: str = "", evidence: str = "", confidence: Optional[float] = None,
                            source: str = "owner", strike: Optional[int] = None) -> None:
        def f(c: sqlite3.Connection) -> None:
            c.execute("INSERT INTO mod_events(user_id,ts,kind,label,confidence,source,action,reason,evidence,strike) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (user_id, time.time(), kind, label, confidence, source[:40], action, reason[:200],
                       evidence[:400], strike))
        await self._run(f)

    async def mod_events_for(self, user_id: int, limit: int = 6) -> List[Dict[str, Any]]:
        def f(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            return [dict(r) for r in c.execute(
                "SELECT ts,kind,label,confidence,source,action,reason,evidence,strike FROM mod_events "
                "WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))]
        return await self._run(f)

    _PERSON = ("p.first_name AS first_name, p.last_name AS last_name, p.username AS username, "
               "p.msg_count AS msg_count, p.first_seen AS first_seen, p.last_seen AS last_seen")

    async def blocked_count(self) -> int:
        return await self._run(lambda c: c.execute("SELECT COUNT(*) FROM blocked").fetchone()[0])

    async def blocked_page(self, offset: int = 0, limit: int = 5) -> List[Dict[str, Any]]:
        def f(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            rows = c.execute(
                f"SELECT b.user_id, b.reason, b.label, b.blocked_at, b.tg_blocked, {self._PERSON}, "
                "(SELECT e.evidence FROM mod_events e WHERE e.user_id=b.user_id AND e.action IN ('block','manual_block') "
                " ORDER BY e.id DESC LIMIT 1) AS evidence, "
                "(SELECT e.kind FROM mod_events e WHERE e.user_id=b.user_id AND e.action IN ('block','manual_block') "
                " ORDER BY e.id DESC LIMIT 1) AS kind, "
                "(SELECT COUNT(*) FROM mod_events e WHERE e.user_id=b.user_id AND e.action IN ('warn','block')) AS offences "
                "FROM blocked b LEFT JOIN user_profiles p ON p.user_id=b.user_id "
                "ORDER BY b.blocked_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            return [dict(r) for r in rows]
        return await self._run(f)

    async def warnings_count(self, ttl_hours: float) -> int:
        return await self._run(lambda c: c.execute("SELECT COUNT(*) FROM warnings WHERE last_ts>=?",
                                                   (time.time() - ttl_hours * 3600,)).fetchone()[0])

    async def warnings_page(self, ttl_hours: float, offset: int = 0, limit: int = 5) -> List[Dict[str, Any]]:
        def f(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            rows = c.execute(
                f"SELECT w.user_id, w.count, w.last_ts, w.reason, {self._PERSON}, "
                "(SELECT e.evidence FROM mod_events e WHERE e.user_id=w.user_id AND e.action='warn' "
                " ORDER BY e.id DESC LIMIT 1) AS evidence, "
                "(SELECT e.kind FROM mod_events e WHERE e.user_id=w.user_id AND e.action='warn' "
                " ORDER BY e.id DESC LIMIT 1) AS kind "
                "FROM warnings w LEFT JOIN user_profiles p ON p.user_id=w.user_id "
                "WHERE w.last_ts>=? ORDER BY w.last_ts DESC LIMIT ? OFFSET ?",
                (time.time() - ttl_hours * 3600, limit, offset)).fetchall()
            return [dict(r) for r in rows]
        return await self._run(f)

    async def clear_warning(self, user_id: int) -> bool:
        def f(c: sqlite3.Connection) -> bool:
            return bool(c.execute("DELETE FROM warnings WHERE user_id=?", (user_id,)).rowcount)
        return await self._run(f)

    async def log_moderation(self, user_id: int, label: str, confidence: float, source: str, action: str) -> None:
        def f(c: sqlite3.Connection) -> None:
            c.execute("INSERT INTO mod_log(user_id,ts,label,confidence,source,action) VALUES(?,?,?,?,?,?)",
                      (user_id, time.time(), label, confidence, source, action))
        await self._run(f)

    async def forget_user(self, user_id: int) -> Dict[str, int]:
        def f(c: sqlite3.Connection) -> Dict[str, int]:
            out = {}
            out["messages"] = c.execute("DELETE FROM messages WHERE user_id=?", (user_id,)).rowcount
            out["facts"] = c.execute("DELETE FROM facts WHERE user_id=?", (user_id,)).rowcount
            out["summary"] = c.execute("DELETE FROM summaries WHERE user_id=?", (user_id,)).rowcount
            return out
        return await self._run(f)

    async def forget_everything(self, user_id: int) -> Dict[str, int]:
        def f(c: sqlite3.Connection) -> Dict[str, int]:
            out = {}
            for key, table in (("messages", "messages"), ("facts", "facts"), ("summary", "summaries"),
                               ("profile", "user_profiles"), ("warnings", "warnings"), ("blocked", "blocked"),
                               ("events", "mod_events"), ("log", "mod_log"), ("vip", "trusted_users")):
                out[key] = c.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,)).rowcount
            return out
        return await self._run(f)

    async def get_meta(self, key: str, default: str = "") -> str:
        def f(c: sqlite3.Connection) -> str:
            r = c.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
            return r[0] if r else default
        return await self._run(f)

    async def set_meta(self, key: str, value: str) -> None:
        await self._run(lambda c: c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (key, value)))

    async def clear_all_memory(self) -> None:
        def f(c: sqlite3.Connection) -> None:
            c.execute("DELETE FROM messages")
            c.execute("DELETE FROM facts")
            c.execute("DELETE FROM summaries")
        await self._run(f)

    async def cleanup(self, message_ttl_days: float, hard_ttl_days: Optional[float] = None) -> Dict[str, int]:
        def f(c: sqlite3.Connection) -> Dict[str, int]:
            now = time.time()
            out = {}
            cut = now - message_ttl_days * 86400
            if hard_ttl_days and hard_ttl_days > message_ttl_days:
                out["messages"] = c.execute(
                    "DELETE FROM messages WHERE ts<? AND (ts<? OR id<=COALESCE("
                    "(SELECT s.covered_msg_id FROM summaries s WHERE s.user_id=messages.user_id),0))",
                    (cut, now - hard_ttl_days * 86400)).rowcount
            else:
                out["messages"] = c.execute("DELETE FROM messages WHERE ts<?", (cut,)).rowcount
            out["facts"] = c.execute("DELETE FROM facts WHERE expires<?", (now,)).rowcount
            out["mod_log"] = c.execute("DELETE FROM mod_log WHERE ts<?", (now - 90 * 86400,)).rowcount
            out["mod_events"] = c.execute("DELETE FROM mod_events WHERE ts<?", (now - 365 * 86400,)).rowcount
            c.execute("DELETE FROM warnings WHERE last_ts<?", (now - 7 * 86400,))
            return out
        return await self._run(f)

    async def users_with_expired_messages(self, message_ttl_days: float, limit: int = 12) -> List[int]:
        def f(c: sqlite3.Connection) -> List[int]:
            cut = time.time() - message_ttl_days * 86400
            rows = c.execute(
                "SELECT m.user_id FROM messages m LEFT JOIN summaries s ON s.user_id=m.user_id "
                "WHERE m.ts<? AND m.id>COALESCE(s.covered_msg_id,0) GROUP BY m.user_id "
                "ORDER BY MIN(m.ts) LIMIT ?", (cut, limit)).fetchall()
            return [int(r[0]) for r in rows]
        return await self._run(f)

    async def expired_messages(self, user_id: int, message_ttl_days: float, after_id: int, limit: int = 80) -> List[Dict[str, Any]]:
        def f(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            cut = time.time() - message_ttl_days * 86400
            rows = c.execute("SELECT id, role, content FROM messages WHERE user_id=? AND id>? AND ts<? ORDER BY id LIMIT ?",
                             (user_id, after_id, cut, limit)).fetchall()
            return [dict(r) for r in rows]
        return await self._run(f)

    async def stats(self) -> Dict[str, int]:
        def f(c: sqlite3.Connection) -> Dict[str, int]:
            g = lambda t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            return {"messages": g("messages"), "facts": g("facts"), "summaries": g("summaries"),
                    "warnings": g("warnings"), "blocked": g("blocked"), "profiles": g("user_profiles"),
                    "trusted": g("trusted_users"),
                    "users": c.execute("SELECT COUNT(DISTINCT user_id) FROM messages").fetchone()[0]}
        return await self._run(f)

    async def user_overview(self, user_id: int) -> Dict[str, Any]:
        facts = await self.get_facts(user_id)
        summary, _ = await self.get_summary(user_id)
        return {"messages": await self.message_count(user_id), "facts": facts, "summary": summary,
                "blocked": await self.is_blocked(user_id)}

    async def integrity_ok(self) -> bool:
        def f(c: sqlite3.Connection) -> bool:
            return c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        return await self._run(f)


def adopt_legacy_db(new_path: str, legacy_path: str = "ai_memory.db") -> bool:
    try:
        if os.path.abspath(new_path) != os.path.abspath(legacy_path) and not os.path.exists(new_path) \
                and os.path.exists(legacy_path):
            os.makedirs(os.path.dirname(os.path.abspath(new_path)), exist_ok=True)
            shutil.copy2(legacy_path, new_path)
            return True
    except OSError:
        pass
    return False
