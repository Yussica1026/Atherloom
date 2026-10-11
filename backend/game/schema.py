from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path


GAME_SCHEMA_VERSION = 2
DbPath = Path | Callable[[], Path]


WORLD_GAME_OPERATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS world_game_operations (
  operation_type TEXT NOT NULL,
  operation_scope TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  result_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(operation_type, operation_scope, idempotency_key)
)
"""


def resolve_db_path(db_path: DbPath) -> Path:
    return db_path() if callable(db_path) else db_path


def connect(db_path: DbPath) -> sqlite3.Connection:
    connection = sqlite3.connect(resolve_db_path(db_path), timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


MIGRATION_1 = (
    """
    CREATE TABLE world_game_worlds (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      current_version INTEGER NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE world_game_versions (
      id TEXT PRIMARY KEY,
      world_id TEXT NOT NULL,
      version INTEGER NOT NULL,
      definition_json TEXT NOT NULL,
      definition_hash TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(world_id, version),
      FOREIGN KEY(world_id) REFERENCES world_game_worlds(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE world_game_sessions (
      id TEXT PRIMARY KEY,
      world_id TEXT NOT NULL,
      world_version_id TEXT NOT NULL,
      branch_name TEXT NOT NULL,
      parent_session_id TEXT,
      parent_revision INTEGER,
      player_id TEXT NOT NULL,
      current_revision INTEGER NOT NULL DEFAULT 0,
      initial_state_json TEXT NOT NULL,
      current_state_json TEXT NOT NULL,
      state_hash TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      FOREIGN KEY(world_id) REFERENCES world_game_worlds(id),
      FOREIGN KEY(world_version_id) REFERENCES world_game_versions(id),
      FOREIGN KEY(parent_session_id) REFERENCES world_game_sessions(id)
    )
    """,
    """
    CREATE TABLE world_game_players (
      session_id TEXT PRIMARY KEY,
      actor_id TEXT NOT NULL,
      display_name TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(session_id, actor_id),
      FOREIGN KEY(session_id) REFERENCES world_game_sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE world_game_ai_characters (
      session_id TEXT NOT NULL,
      actor_id TEXT NOT NULL,
      persona_id TEXT NOT NULL,
      provider_id TEXT NOT NULL,
      persona_snapshot_json TEXT NOT NULL,
      config_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL,
      PRIMARY KEY(session_id, actor_id),
      FOREIGN KEY(session_id) REFERENCES world_game_sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE world_game_npcs (
      session_id TEXT NOT NULL,
      actor_id TEXT NOT NULL,
      definition_id TEXT NOT NULL,
      created_at TEXT NOT NULL,
      PRIMARY KEY(session_id, actor_id),
      FOREIGN KEY(session_id) REFERENCES world_game_sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE world_game_turns (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      revision INTEGER NOT NULL,
      idempotency_key TEXT NOT NULL,
      request_hash TEXT NOT NULL,
      actor_kind TEXT NOT NULL,
      actor_id TEXT NOT NULL,
      intent_json TEXT NOT NULL,
      content TEXT NOT NULL DEFAULT '',
      expected_revision INTEGER NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(session_id, revision),
      UNIQUE(session_id, idempotency_key),
      FOREIGN KEY(session_id) REFERENCES world_game_sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE world_game_events (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      turn_id TEXT NOT NULL,
      revision INTEGER NOT NULL,
      sequence INTEGER NOT NULL,
      event_type TEXT NOT NULL,
      event_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(session_id, revision, sequence),
      FOREIGN KEY(session_id) REFERENCES world_game_sessions(id) ON DELETE CASCADE,
      FOREIGN KEY(turn_id) REFERENCES world_game_turns(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE world_game_snapshots (
      session_id TEXT NOT NULL,
      revision INTEGER NOT NULL,
      state_json TEXT NOT NULL,
      state_hash TEXT NOT NULL,
      created_at TEXT NOT NULL,
      PRIMARY KEY(session_id, revision),
      FOREIGN KEY(session_id) REFERENCES world_game_sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE world_game_saves (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      revision INTEGER NOT NULL,
      name TEXT NOT NULL,
      state_json TEXT NOT NULL,
      state_hash TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(session_id) REFERENCES world_game_sessions(id) ON DELETE CASCADE
    )
    """,
    WORLD_GAME_OPERATIONS_TABLE,
    "CREATE INDEX world_game_versions_world ON world_game_versions(world_id, version DESC)",
    "CREATE INDEX world_game_sessions_world ON world_game_sessions(world_id, updated_at DESC)",
    "CREATE INDEX world_game_events_replay ON world_game_events(session_id, revision, sequence)",
    "CREATE INDEX world_game_saves_session ON world_game_saves(session_id, created_at DESC)",
)


MIGRATION_2 = (
    """
    CREATE TABLE IF NOT EXISTS world_game_turn_outputs (
      turn_id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      output_json TEXT NOT NULL,
      output_hash TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(turn_id) REFERENCES world_game_turns(id) ON DELETE CASCADE,
      FOREIGN KEY(session_id) REFERENCES world_game_sessions(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS world_game_turn_outputs_session ON world_game_turn_outputs(session_id, turn_id)",
)


def init_schema(db_path: DbPath) -> None:
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS world_game_schema_meta (
                  singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
                  version INTEGER NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT version FROM world_game_schema_meta WHERE singleton_id=1"
            ).fetchone()
            current_version = int(row["version"]) if row else 0
            if current_version > GAME_SCHEMA_VERSION:
                raise RuntimeError(
                    f"长期世界数据库版本 {current_version} 高于当前程序支持的 {GAME_SCHEMA_VERSION}"
                )
            if current_version < 1:
                for statement in MIGRATION_1:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO world_game_schema_meta(singleton_id,version) VALUES(1,1)"
                )
                current_version = 1
            if current_version < 2:
                for statement in MIGRATION_2:
                    connection.execute(statement)
                connection.execute(
                    "UPDATE world_game_schema_meta SET version=2 WHERE singleton_id=1"
                )
                current_version = 2
            # Some early developer v1 databases predate the operations table.
            # Keep that narrowly scoped compatibility repair idempotent.
            connection.execute(WORLD_GAME_OPERATIONS_TABLE)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
