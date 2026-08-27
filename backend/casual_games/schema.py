from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path


CASUAL_GAME_SCHEMA_VERSION = 2
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
    CREATE TABLE casual_game_sessions (
      id TEXT PRIMARY KEY,
      game_id TEXT NOT NULL,
      rules_version INTEGER NOT NULL CHECK(rules_version >= 1),
      conversation_id TEXT NOT NULL,
      persona_id TEXT NOT NULL,
      player_id TEXT NOT NULL,
      creation_key TEXT,
      creation_request_json TEXT,
      state_json TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('active','finished','abandoned')),
      revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
      result_id TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      finished_at TEXT,
      UNIQUE(conversation_id, creation_key)
    )
    """,
    """
    CREATE TABLE casual_game_history (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      revision INTEGER NOT NULL CHECK(revision >= 1),
      actor_kind TEXT NOT NULL CHECK(actor_kind IN ('user','persona')),
      actor_id TEXT NOT NULL,
      idempotency_key TEXT NOT NULL,
      expected_revision INTEGER NOT NULL CHECK(expected_revision >= 0),
      action_json TEXT NOT NULL,
      events_json TEXT NOT NULL,
      response_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(session_id, revision),
      UNIQUE(session_id, idempotency_key),
      FOREIGN KEY(session_id) REFERENCES casual_game_sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE casual_game_results (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL UNIQUE,
      result_json TEXT NOT NULL,
      chat_message_id TEXT,
      memory_id TEXT,
      memory_decision TEXT CHECK(memory_decision IN ('approved','declined')),
      created_at TEXT NOT NULL,
      FOREIGN KEY(session_id) REFERENCES casual_game_sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE casual_game_behavior_configs (
      persona_id TEXT NOT NULL,
      game_id TEXT NOT NULL,
      instructions TEXT,
      strategy_instructions TEXT,
      reaction_instructions TEXT,
      memory_instructions TEXT,
      memory_mode TEXT NOT NULL DEFAULT 'ask' CHECK(memory_mode IN ('off','ask','auto')),
      updated_at TEXT NOT NULL,
      PRIMARY KEY(persona_id, game_id)
    )
    """,
    "CREATE INDEX casual_game_sessions_conversation ON casual_game_sessions(conversation_id, updated_at DESC)",
    "CREATE INDEX casual_game_sessions_persona ON casual_game_sessions(persona_id, updated_at DESC)",
    "CREATE INDEX casual_game_history_session ON casual_game_history(session_id, revision)",
)


def init_schema(db_path: DbPath) -> None:
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS casual_game_schema_meta (
                  singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
                  version INTEGER NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT version FROM casual_game_schema_meta WHERE singleton_id=1"
            ).fetchone()
            current_version = int(row["version"]) if row else 0
            if current_version > CASUAL_GAME_SCHEMA_VERSION:
                raise RuntimeError(
                    f"休闲游戏数据库版本 {current_version} 高于当前程序支持的 "
                    f"{CASUAL_GAME_SCHEMA_VERSION}"
                )
            if current_version == 0:
                for statement in SCHEMA_STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO casual_game_schema_meta(singleton_id,version) VALUES(1,?)",
                    (CASUAL_GAME_SCHEMA_VERSION,),
                )
            elif current_version == 1:
                connection.execute(
                    "ALTER TABLE casual_game_results ADD COLUMN chat_message_id TEXT"
                )
                connection.execute(
                    "ALTER TABLE casual_game_results ADD COLUMN memory_id TEXT"
                )
                connection.execute(
                    """
                    ALTER TABLE casual_game_results
                    ADD COLUMN memory_decision TEXT
                    CHECK(memory_decision IN ('approved','declined'))
                    """
                )
                connection.execute(
                    "UPDATE casual_game_schema_meta SET version=? WHERE singleton_id=1",
                    (CASUAL_GAME_SCHEMA_VERSION,),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
