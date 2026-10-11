from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path


DbPath = Path | Callable[[], Path]


def resolve_db_path(db_path: DbPath) -> Path:
    return db_path() if callable(db_path) else db_path


def connect(db_path: DbPath) -> sqlite3.Connection:
    connection = sqlite3.connect(resolve_db_path(db_path), timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS import_batches (
      id TEXT PRIMARY KEY,
      format_id TEXT NOT NULL,
      platform TEXT NOT NULL,
      source_name TEXT,
      status TEXT NOT NULL CHECK(status IN ('previewed','committed','rolled_back')),
      canonical_json TEXT NOT NULL,
      warnings_json TEXT NOT NULL DEFAULT '[]',
      conversation_count INTEGER NOT NULL DEFAULT 0,
      message_count INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      committed_at TEXT,
      rolled_back_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_conversation_provenance (
      batch_id TEXT NOT NULL,
      conversation_id TEXT NOT NULL UNIQUE,
      source_conversation_id TEXT,
      canonical_conversation_id TEXT NOT NULL,
      provenance_json TEXT NOT NULL,
      imported_message_count INTEGER NOT NULL DEFAULT 0,
      created_by_batch INTEGER NOT NULL DEFAULT 1 CHECK(created_by_batch=1),
      created_at TEXT NOT NULL,
      PRIMARY KEY(batch_id, conversation_id),
      FOREIGN KEY(batch_id) REFERENCES import_batches(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_message_provenance (
      batch_id TEXT NOT NULL,
      message_id TEXT NOT NULL UNIQUE,
      conversation_id TEXT NOT NULL,
      source_conversation_id TEXT,
      canonical_conversation_id TEXT NOT NULL,
      source_node_id TEXT NOT NULL,
      source_message_id TEXT,
      provenance_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      PRIMARY KEY(batch_id, message_id),
      FOREIGN KEY(batch_id) REFERENCES import_batches(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS import_conversation_batch
      ON import_conversation_provenance(batch_id, conversation_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS import_message_batch_conversation
      ON import_message_provenance(batch_id, conversation_id)
    """,
)


def init_schema(db_path: DbPath) -> None:
    with closing(connect(db_path)) as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.commit()
