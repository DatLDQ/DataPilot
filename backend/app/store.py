"""SQLite persistence for dataset metadata, conversations and agent history."""

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    created_at TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    suggestions_json TEXT
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- Exact Claude API message history (append-only, replayed on every turn).
CREATE TABLE IF NOT EXISTS api_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL
);
-- UI event log per question, replayed by the frontend to rebuild the timeline.
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    events_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_dataset ON conversations(dataset_id);
CREATE INDEX IF NOT EXISTS idx_msgs_conv ON api_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_turns_conv ON turns(conversation_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, params)

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # -- datasets ---------------------------------------------------------
    def add_dataset(self, dataset_id: str, name: str, filename: str, profile: dict) -> dict:
        self._exec(
            "INSERT INTO datasets (id, name, filename, created_at, profile_json) VALUES (?, ?, ?, ?, ?)",
            (dataset_id, name, filename, _now(), json.dumps(profile)),
        )
        return self.get_dataset(dataset_id)  # type: ignore[return-value]

    def list_datasets(self) -> list[dict]:
        rows = self._query("SELECT * FROM datasets ORDER BY created_at DESC")
        return [self._dataset_row(r) for r in rows]

    def get_dataset(self, dataset_id: str) -> dict | None:
        rows = self._query("SELECT * FROM datasets WHERE id = ?", (dataset_id,))
        return self._dataset_row(rows[0]) if rows else None

    def set_suggestions(self, dataset_id: str, suggestions: list[str]) -> None:
        self._exec("UPDATE datasets SET suggestions_json = ? WHERE id = ?", (json.dumps(suggestions), dataset_id))

    def delete_dataset(self, dataset_id: str) -> None:
        self._exec("DELETE FROM datasets WHERE id = ?", (dataset_id,))

    @staticmethod
    def _dataset_row(r: sqlite3.Row) -> dict:
        return {
            "id": r["id"],
            "name": r["name"],
            "filename": r["filename"],
            "created_at": r["created_at"],
            "profile": json.loads(r["profile_json"]),
            "suggestions": json.loads(r["suggestions_json"]) if r["suggestions_json"] else None,
        }

    # -- conversations ----------------------------------------------------
    def create_conversation(self, dataset_id: str, title: str = "New analysis") -> dict:
        conv_id = uuid.uuid4().hex[:12]
        now = _now()
        self._exec(
            "INSERT INTO conversations (id, dataset_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conv_id, dataset_id, title, now, now),
        )
        return self.get_conversation(conv_id)  # type: ignore[return-value]

    def get_conversation(self, conv_id: str) -> dict | None:
        rows = self._query("SELECT * FROM conversations WHERE id = ?", (conv_id,))
        return dict(rows[0]) if rows else None

    def list_conversations(self, dataset_id: str | None = None) -> list[dict]:
        if dataset_id:
            rows = self._query(
                "SELECT * FROM conversations WHERE dataset_id = ? ORDER BY updated_at DESC", (dataset_id,)
            )
        else:
            rows = self._query("SELECT * FROM conversations ORDER BY updated_at DESC")
        return [dict(r) for r in rows]

    def rename_conversation(self, conv_id: str, title: str) -> None:
        self._exec("UPDATE conversations SET title = ? WHERE id = ?", (title, conv_id))

    def touch_conversation(self, conv_id: str) -> None:
        self._exec("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conv_id))

    def delete_conversation(self, conv_id: str) -> None:
        self._exec("DELETE FROM conversations WHERE id = ?", (conv_id,))

    # -- history ----------------------------------------------------------
    def get_api_messages(self, conv_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT role, content_json FROM api_messages WHERE conversation_id = ? ORDER BY id", (conv_id,)
        )
        return [{"role": r["role"], "content": json.loads(r["content_json"])} for r in rows]

    def append_api_messages(self, conv_id: str, messages: list[dict[str, Any]]) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO api_messages (conversation_id, role, content_json) VALUES (?, ?, ?)",
                [(conv_id, m["role"], json.dumps(m["content"])) for m in messages],
            )

    def add_turn(self, conv_id: str, question: str, events: list[dict]) -> None:
        self._exec(
            "INSERT INTO turns (conversation_id, question, events_json, created_at) VALUES (?, ?, ?, ?)",
            (conv_id, question, json.dumps(events), _now()),
        )

    def get_turns(self, conv_id: str) -> list[dict]:
        rows = self._query("SELECT * FROM turns WHERE conversation_id = ? ORDER BY id", (conv_id,))
        return [
            {"id": r["id"], "question": r["question"], "events": json.loads(r["events_json"]),
             "created_at": r["created_at"]}
            for r in rows
        ]
