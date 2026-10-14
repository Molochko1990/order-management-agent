import json
import sqlite3
import time
import uuid

import support_tools

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages (session_id TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
                                     PRIMARY KEY (session_id, seq));
CREATE TABLE IF NOT EXISTS usage (session_id TEXT NOT NULL, ts REAL NOT NULL, input INTEGER, cached INTEGER,
                                  cache_write INTEGER, output INTEGER, reasoning INTEGER, cost_usd REAL);
CREATE TABLE IF NOT EXISTS working_copy (name TEXT PRIMARY KEY, payload TEXT NOT NULL);
"""

_db: sqlite3.Connection | None = None


def open_db(path: str) -> None:
    global _db
    if _db is not None:
        _db.close()
    _db = sqlite3.connect(path, check_same_thread=False)
    _db.executescript(SCHEMA)
    rows = dict(_db.execute("SELECT name, payload FROM working_copy"))
    if rows:
        support_tools.ORDERS.clear()
        support_tools.ORDERS.update({int(k): v for k, v in json.loads(rows["orders"]).items()})
        support_tools.REFUNDS[:] = json.loads(rows["refunds"])
    else:
        support_tools.reset_state()
        with _db:
            _write_working_copy()


def reset_db() -> None:
    with _db:
        for table in ("sessions", "messages", "usage", "working_copy"):
            _db.execute(f"DELETE FROM {table}")
        support_tools.reset_state()
        _write_working_copy()


def _write_working_copy() -> None:
    _db.execute("REPLACE INTO working_copy VALUES ('orders', ?)", (json.dumps(support_tools.ORDERS, ensure_ascii=False),))
    _db.execute("REPLACE INTO working_copy VALUES ('refunds', ?)", (json.dumps(support_tools.REFUNDS, ensure_ascii=False),))


def create_session(user_id: str) -> str:
    session_id = uuid.uuid4().hex
    with _db:
        _db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (session_id, user_id, time.time()))
    return session_id


def session_user(session_id: str) -> str | None:
    row = _db.execute("SELECT user_id FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    return row[0] if row else None


def load_history(session_id: str) -> list[dict]:
    rows = _db.execute("SELECT payload FROM messages WHERE session_id = ? ORDER BY seq", (session_id,))
    return [json.loads(payload) for (payload,) in rows]


def _insert_messages(session_id: str, items: list[dict]) -> None:
    (last,) = _db.execute("SELECT COALESCE(MAX(seq), 0) FROM messages WHERE session_id = ?", (session_id,)).fetchone()
    _db.executemany(
        "INSERT INTO messages VALUES (?, ?, ?)",
        [(session_id, last + i, json.dumps(item, ensure_ascii=False)) for i, item in enumerate(items, 1)],
    )


def append_messages(session_id: str, items: list[dict]) -> None:
    with _db:
        _insert_messages(session_id, items)


def save_step(session_id: str, items: list[dict]) -> None:
    with _db:
        _insert_messages(session_id, items)
        _write_working_copy()


def log_usage(session_id: str, u: dict) -> None:
    with _db:
        _db.execute(
            "INSERT INTO usage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, time.time(), u["input"], u["cached"], u["cache_write"], u["output"], u["reasoning"], u["cost_usd"]),
        )


def session_cost(session_id: str) -> float:
    (cost,) = _db.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE session_id = ?", (session_id,)).fetchone()
    return cost
