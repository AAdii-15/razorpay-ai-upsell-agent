"""
Append-only, tamper-evident audit log.

Every event (agent decision, guardrail check, human approval, Razorpay call,
failure + recovery) is written here with a hash that chains to the previous
event in the same session. If any row is edited or deleted after the fact,
verify_chain() will detect it — this is what makes the trail an *audit*
trail rather than just a log file.
"""

import sqlite3
import json
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "audit.db"
GENESIS_HASH = "0" * 64


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            payload TEXT NOT NULL,
            reasoning TEXT,
            prev_hash TEXT NOT NULL,
            hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_decisions (
            decision_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


def save_pending_decision(decision_id: str, session_id: str, data: dict) -> None:
    """Persisted, not in-memory — a server restart no longer silently
    drops a decision that's awaiting human sign-off."""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO pending_decisions (decision_id, session_id, data, created_at) VALUES (?, ?, ?, ?)",
            (decision_id, session_id, json.dumps(data, default=str), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def pop_pending_decision(decision_id: str) -> dict | None:
    """Read-then-delete in one call, same 'can't double-resolve' guarantee
    the in-memory dict.pop() gave — a second call always returns None."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT data FROM pending_decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM pending_decisions WHERE decision_id = ?", (decision_id,))
        conn.commit()
        return json.loads(row[0])
    finally:
        conn.close()


def _last_hash(conn, session_id: str) -> str:
    row = conn.execute(
        "SELECT hash FROM audit_log WHERE session_id = ? ORDER BY id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return row[0] if row else GENESIS_HASH


def _compute_hash(event_id, session_id, event_type, actor, payload_json, reasoning, prev_hash, created_at) -> str:
    record = f"{event_id}|{session_id}|{event_type}|{actor}|{payload_json}|{reasoning}|{prev_hash}|{created_at}"
    return hashlib.sha256(record.encode()).hexdigest()


def log_event(session_id: str, event_type: str, actor: str, payload: dict, reasoning: str | None = None) -> dict:
    conn = _get_conn()
    try:
        event_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        prev_hash = _last_hash(conn, session_id)
        payload_json = json.dumps(payload, sort_keys=True, default=str)
        this_hash = _compute_hash(event_id, session_id, event_type, actor, payload_json, reasoning, prev_hash, created_at)

        conn.execute(
            """INSERT INTO audit_log
               (event_id, session_id, event_type, actor, payload, reasoning, prev_hash, hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, session_id, event_type, actor, payload_json, reasoning, prev_hash, this_hash, created_at),
        )
        conn.commit()
        return {
            "event_id": event_id, "session_id": session_id, "event_type": event_type,
            "actor": actor, "payload": payload, "reasoning": reasoning,
            "hash": this_hash, "created_at": created_at,
        }
    finally:
        conn.close()


def get_trail(session_id: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT event_id, event_type, actor, payload, reasoning, prev_hash, hash, created_at
               FROM audit_log WHERE session_id = ? ORDER BY id ASC""",
            (session_id,),
        ).fetchall()
        return [
            {
                "event_id": r[0], "event_type": r[1], "actor": r[2],
                "payload": json.loads(r[3]), "reasoning": r[4],
                "prev_hash": r[5], "hash": r[6], "created_at": r[7],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_all_events(event_type: str | None = None) -> list[dict]:
    """Cross-session query, used for /metrics."""
    conn = _get_conn()
    try:
        if event_type:
            rows = conn.execute(
                """SELECT event_id, session_id, event_type, actor, payload, reasoning, created_at
                   FROM audit_log WHERE event_type = ? ORDER BY id ASC""",
                (event_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT event_id, session_id, event_type, actor, payload, reasoning, created_at
                   FROM audit_log ORDER BY id ASC""",
            ).fetchall()
        return [
            {
                "event_id": r[0], "session_id": r[1], "event_type": r[2],
                "actor": r[3], "payload": json.loads(r[4]), "reasoning": r[5], "created_at": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()


def verify_chain(session_id: str) -> dict:
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT event_id, event_type, actor, payload, reasoning, prev_hash, hash, created_at
               FROM audit_log WHERE session_id = ? ORDER BY id ASC""",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    expected_prev = GENESIS_HASH
    for i, r in enumerate(rows):
        event_id, event_type, actor, payload_json, reasoning, prev_hash, stored_hash, created_at = r
        if prev_hash != expected_prev:
            return {"valid": False, "broken_at_index": i, "reason": "prev_hash mismatch"}
        recomputed = _compute_hash(event_id, session_id, event_type, actor, payload_json, reasoning, prev_hash, created_at)
        if recomputed != stored_hash:
            return {"valid": False, "broken_at_index": i, "reason": "hash mismatch — record was altered"}
        expected_prev = stored_hash

    return {"valid": True, "events_checked": len(rows)}
