"""SQLite storage: store data (customers, orders...) plus agent telemetry (conversations, turns, tickets)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    phone       TEXT,
    city        TEXT
);
CREATE TABLE IF NOT EXISTS products (
    sku         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    price       REAL NOT NULL,
    final_sale  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    status          TEXT NOT NULL,          -- processing | shipped | delivered | cancelled | refunded
    order_date      TEXT NOT NULL,
    ship_date       TEXT,
    delivery_date   TEXT,
    carrier         TEXT,
    tracking_number TEXT,
    total           REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS order_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   TEXT NOT NULL REFERENCES orders(order_id),
    sku        TEXT NOT NULL REFERENCES products(sku),
    qty        INTEGER NOT NULL,
    unit_price REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tracking_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id  TEXT NOT NULL REFERENCES orders(order_id),
    ts        TEXT NOT NULL,
    location  TEXT NOT NULL,
    status    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refunds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT NOT NULL,
    sku         TEXT,
    amount      REAL NOT NULL,
    reason      TEXT,
    created_at  TEXT NOT NULL,
    conversation_id TEXT
);
CREATE TABLE IF NOT EXISTS returns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rma         TEXT NOT NULL UNIQUE,
    order_id    TEXT NOT NULL,
    sku         TEXT NOT NULL,
    reason      TEXT,
    status      TEXT NOT NULL DEFAULT 'label_sent',
    created_at  TEXT NOT NULL,
    conversation_id TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    order_id        TEXT,
    reason          TEXT NOT NULL,
    priority        TEXT NOT NULL,
    summary         TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    channel         TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    outcome         TEXT NOT NULL DEFAULT 'open'   -- open | handled_by_ai | escalated
);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    payload         TEXT NOT NULL,                 -- neutral message JSON (see app/llm.py)
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turn_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    channel         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    latency_ms      REAL NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    llm_calls       INTEGER NOT NULL DEFAULT 0,
    tool_calls      TEXT NOT NULL DEFAULT '[]',
    provider        TEXT,
    model           TEXT,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_turns_conv ON turn_logs(conversation_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: Path | str) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ---------------- conversation helpers ----------------

def ensure_conversation(conn: sqlite3.Connection, conversation_id: str, channel: str) -> None:
    ts = now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO conversations (conversation_id, channel, started_at, updated_at) VALUES (?,?,?,?)",
        (conversation_id, channel, ts, ts),
    )
    conn.commit()


def set_outcome(conn: sqlite3.Connection, conversation_id: str, outcome: str) -> None:
    conn.execute(
        "UPDATE conversations SET outcome = ?, updated_at = ? WHERE conversation_id = ?",
        (outcome, now_iso(), conversation_id),
    )
    conn.commit()


def get_outcome(conn: sqlite3.Connection, conversation_id: str) -> str:
    row = conn.execute("SELECT outcome FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
    return row["outcome"] if row else "open"


def load_messages(conn: sqlite3.Connection, conversation_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT payload FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)
    ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def append_messages(conn: sqlite3.Connection, conversation_id: str, msgs: list[dict]) -> None:
    ts = now_iso()
    conn.executemany(
        "INSERT INTO messages (conversation_id, payload, created_at) VALUES (?,?,?)",
        [(conversation_id, json.dumps(m, ensure_ascii=False), ts) for m in msgs],
    )
    conn.commit()


def log_turn(conn: sqlite3.Connection, **row) -> None:
    row.setdefault("created_at", now_iso())
    row["tool_calls"] = json.dumps(row.get("tool_calls", []), ensure_ascii=False)
    cols = ", ".join(row.keys())
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO turn_logs ({cols}) VALUES ({marks})", tuple(row.values()))
    conn.execute("UPDATE conversations SET updated_at = ? WHERE conversation_id = ?", (now_iso(), row["conversation_id"]))
    conn.commit()
