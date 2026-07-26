from __future__ import annotations

import json
import hashlib
import html as html_lib
import math
import asyncio
import binascii
import os
import re
import sqlite3
import uuid
from urllib.parse import parse_qs, unquote, urlparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from cryptography.exceptions import InvalidTag
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.motivation import DRIVES, EVENTS, apply_event, context_summary, default_state, normalize, tick
from backend.health import (
    decrypt_sync_envelope,
    encrypt_for_storage,
    health_enabled,
    load_health_summaries,
    normalize_health_payload,
)

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DB_PATH = ROOT / "data" / "local.db"
DEFAULT_SUMMARY_PROMPT = """请把下面这段较早的对话压缩成连续、忠实、可供后续聊天使用的摘要。\n\n要求：\n1. 保留人物关系、关键事实、决定、承诺、未完成事项和情绪变化。\n2. 不编造双方没有表达过的心意或事实。\n3. 区分用户与助手各自说过的话。\n4. 删除寒暄、重复和已经失效的临时细节。\n5. 使用简洁中文，不评价用户。\n\n会话标题：{{title}}\n已有摘要：{{existing_summary}}\n待总结对话：\n{{conversation}}"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(db()) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS providers (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, protocol TEXT NOT NULL,
              base_url TEXT NOT NULL, api_key TEXT NOT NULL DEFAULT '', model TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1, custom_headers TEXT NOT NULL DEFAULT '{}',
              prompt_cache INTEGER NOT NULL DEFAULT 1, thinking_enabled INTEGER NOT NULL DEFAULT 1,
              stream_enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
              vision_mode TEXT NOT NULL DEFAULT 'auto',
              cache_mode TEXT NOT NULL DEFAULT 'auto',
              prompt_cache_key TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS personas (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS persona_configs (
              persona_id TEXT PRIMARY KEY, config_json TEXT NOT NULL DEFAULT '{}',
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, provider_id TEXT,
              persona_id TEXT, summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
              starred INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
              content TEXT NOT NULL, provider_id TEXT, model TEXT, created_at TEXT NOT NULL,
              reasoning TEXT NOT NULL DEFAULT '', parent_message_id TEXT
            );
            CREATE TABLE IF NOT EXISTS message_trash (
              message_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS message_selections (
              conversation_id TEXT NOT NULL, parent_message_id TEXT NOT NULL,
              assistant_message_id TEXT NOT NULL,
              PRIMARY KEY(conversation_id, parent_message_id)
            );
            CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memories (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
              kind TEXT NOT NULL DEFAULT 'fact', source_conversation_id TEXT,
              source_message_id TEXT, starred INTEGER NOT NULL DEFAULT 0,
              archived INTEGER NOT NULL DEFAULT 0, deleted_at TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_audit (
              id TEXT PRIMARY KEY, memory_id TEXT NOT NULL, action TEXT NOT NULL,
              detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS summary_versions (
              id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, content TEXT NOT NULL,
              source TEXT NOT NULL DEFAULT 'manual', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS motivation_states (
              persona_key TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
              state_json TEXT NOT NULL, offline_mode TEXT NOT NULL DEFAULT 'limited',
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS journal_entries (
              id TEXT PRIMARY KEY, persona_key TEXT NOT NULL, title TEXT NOT NULL,
              content TEXT NOT NULL, space TEXT NOT NULL DEFAULT 'user',
              author TEXT NOT NULL DEFAULT 'user', visible_to_user INTEGER NOT NULL DEFAULT 1,
              visible_to_ai INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS board_messages (
              id TEXT PRIMARY KEY, persona_key TEXT NOT NULL, content TEXT NOT NULL,
              author TEXT NOT NULL DEFAULT 'user', visible_to_user INTEGER NOT NULL DEFAULT 1,
              visible_to_ai INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS game_saves (
              game_id TEXT NOT NULL, persona_key TEXT NOT NULL, state_json TEXT NOT NULL,
              updated_at TEXT NOT NULL, PRIMARY KEY(game_id, persona_key)
            );
            CREATE TABLE IF NOT EXISTS favorites (
              id TEXT PRIMARY KEY, source_message_id TEXT NOT NULL UNIQUE,
              conversation_id TEXT NOT NULL, role TEXT NOT NULL,
              text_snapshot TEXT NOT NULL, conversation_title_snapshot TEXT NOT NULL DEFAULT '',
              original_message_created_at TEXT NOT NULL, favorited_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS favorite_owners (
              favorite_id TEXT NOT NULL, owner TEXT NOT NULL,
              created_at TEXT NOT NULL, PRIMARY KEY(favorite_id, owner),
              FOREIGN KEY(favorite_id) REFERENCES favorites(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS favorites_order ON favorites(favorited_at DESC, id DESC);
            CREATE TABLE IF NOT EXISTS worldbooks (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
              enabled INTEGER NOT NULL DEFAULT 1, entries_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mcp_servers (
              id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, url TEXT NOT NULL,
              token TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
              last_status TEXT NOT NULL DEFAULT '', last_detail TEXT NOT NULL DEFAULT '',
              last_tested_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mcp_audit (
              id TEXT PRIMARY KEY, server_id TEXT NOT NULL, tool_name TEXT NOT NULL,
              status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
              conversation_id TEXT, user_message_id TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS health_daily_summaries (
              id TEXT PRIMARY KEY, device_id TEXT NOT NULL, day TEXT NOT NULL,
              nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, updated_at TEXT NOT NULL,
              UNIQUE(device_id, day)
            );
            CREATE TABLE IF NOT EXISTS health_sync_audit (
              id TEXT PRIMARY KEY, device_id TEXT NOT NULL, day TEXT NOT NULL,
              status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS roleplay_stories (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, player_name TEXT NOT NULL,
              premise TEXT NOT NULL DEFAULT '', narrator_provider_id TEXT NOT NULL,
              cast_json TEXT NOT NULL DEFAULT '[]', state_json TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS roleplay_turns (
              id TEXT PRIMARY KEY, story_id TEXT NOT NULL, turn_number INTEGER NOT NULL,
              player_input TEXT NOT NULL, actor_drafts_json TEXT NOT NULL DEFAULT '[]',
              prose TEXT NOT NULL, checkpoint_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL, UNIQUE(story_id, turn_number)
            );
            CREATE INDEX IF NOT EXISTS health_daily_day ON health_daily_summaries(day DESC);
            CREATE INDEX IF NOT EXISTS roleplay_story_updated ON roleplay_stories(updated_at DESC);
            CREATE INDEX IF NOT EXISTS roleplay_turn_story ON roleplay_turns(story_id, turn_number);
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(providers)")}
        if "custom_headers" not in columns:
            connection.execute("ALTER TABLE providers ADD COLUMN custom_headers TEXT NOT NULL DEFAULT '{}'")
        if "prompt_cache" not in columns:
            connection.execute("ALTER TABLE providers ADD COLUMN prompt_cache INTEGER NOT NULL DEFAULT 1")
        if "thinking_enabled" not in columns:
            connection.execute("ALTER TABLE providers ADD COLUMN thinking_enabled INTEGER NOT NULL DEFAULT 1")
        if "stream_enabled" not in columns:
            connection.execute("ALTER TABLE providers ADD COLUMN stream_enabled INTEGER NOT NULL DEFAULT 1")
        if "temperature" not in columns:
            connection.execute("ALTER TABLE providers ADD COLUMN temperature REAL NOT NULL DEFAULT 0.7")
        if "top_p" not in columns:
            connection.execute("ALTER TABLE providers ADD COLUMN top_p REAL NOT NULL DEFAULT 1.0")
        if "max_tokens" not in columns:
            connection.execute("ALTER TABLE providers ADD COLUMN max_tokens INTEGER NOT NULL DEFAULT 4096")
        if "vision_mode" not in columns:
            connection.execute("ALTER TABLE providers ADD COLUMN vision_mode TEXT NOT NULL DEFAULT 'auto'")
        if "cache_mode" not in columns:
            connection.execute("ALTER TABLE providers ADD COLUMN cache_mode TEXT NOT NULL DEFAULT 'auto'")
        if "prompt_cache_key" not in columns:
            connection.execute("ALTER TABLE providers ADD COLUMN prompt_cache_key TEXT NOT NULL DEFAULT ''")
        motivation_columns = {row["name"] for row in connection.execute("PRAGMA table_info(motivation_states)")}
        if "offline_mode" not in motivation_columns:
            connection.execute("ALTER TABLE motivation_states ADD COLUMN offline_mode TEXT NOT NULL DEFAULT 'limited'")
        message_columns = {row["name"] for row in connection.execute("PRAGMA table_info(messages)")}
        if "reasoning" not in message_columns:
            connection.execute("ALTER TABLE messages ADD COLUMN reasoning TEXT NOT NULL DEFAULT ''")
        if "parent_message_id" not in message_columns:
            connection.execute("ALTER TABLE messages ADD COLUMN parent_message_id TEXT")
        conversation_columns = {row["name"] for row in connection.execute("PRAGMA table_info(conversations)")}
        for column in ("pinned", "starred", "archived"):
            if column not in conversation_columns:
                connection.execute(f"ALTER TABLE conversations ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        mcp_columns = {row["name"] for row in connection.execute("PRAGMA table_info(mcp_servers)")}
        for column, definition in {
            "transport": "TEXT NOT NULL DEFAULT 'http'",
            "command": "TEXT NOT NULL DEFAULT ''",
            "args_json": "TEXT NOT NULL DEFAULT '[]'",
            "env_json": "TEXT NOT NULL DEFAULT '{}'",
            "headers_json": "TEXT NOT NULL DEFAULT '{}'",
            "tools_json": "TEXT NOT NULL DEFAULT '[]'",
            "tool_policy_json": "TEXT NOT NULL DEFAULT '{}'",
        }.items():
            if column not in mcp_columns:
                connection.execute(f"ALTER TABLE mcp_servers ADD COLUMN {column} {definition}")
        mcp_audit_columns = {row["name"] for row in connection.execute("PRAGMA table_info(mcp_audit)")}
        if "conversation_id" not in mcp_audit_columns:
            connection.execute("ALTER TABLE mcp_audit ADD COLUMN conversation_id TEXT")
        if "user_message_id" not in mcp_audit_columns:
            connection.execute("ALTER TABLE mcp_audit ADD COLUMN user_message_id TEXT")
        connection.commit()


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    protocol: str = Field(pattern="^(openai|anthropic|deepseek|glm)$")
    base_url: str
    api_key: str = ""
    model: str
    enabled: bool = True
    custom_headers: str = "{}"
    prompt_cache: bool = True
    thinking_enabled: bool = True
    stream_enabled: bool = True
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=1.0, ge=0, le=1)
    max_tokens: int = Field(default=4096, ge=1, le=200000)
    vision_mode: str = Field(default="auto", pattern="^(auto|openai|anthropic|text)$")
    cache_mode: str = Field(default="auto", pattern="^(auto|off|anthropic|openai)$")
    prompt_cache_key: str = Field(default="", max_length=200)
    source_provider_id: str | None = None


class ProviderProbe(BaseModel):
    protocol: str = Field(pattern="^(openai|anthropic|deepseek|glm)$")
    base_url: str
    api_key: str = ""
    custom_headers: str = "{}"
    provider_id: str | None = None


class MessageSelectionIn(BaseModel):
    conversation_id: str
    parent_message_id: str
    assistant_message_id: str


class MessageEditIn(BaseModel):
    content: str = Field(min_length=1, max_length=200000)


class PersonaIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    prompt: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class WorldbookIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True
    entries: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


class McpServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    transport: str = Field(default="http", pattern="^(http|stdio)$")
    url: str = Field(default="", max_length=2000)
    token: str = Field(default="", max_length=4000)
    command: str = Field(default="", max_length=1000)
    args: list[str] = Field(default_factory=list, max_length=100)
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    tool_policies: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class ConversationIn(BaseModel):
    title: str = "新对话"
    provider_id: str | None = None
    persona_id: str | None = None


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class ConversationState(BaseModel):
    pinned: bool | None = None
    starred: bool | None = None
    archived: bool | None = None


class AppSettingsIn(BaseModel):
    auto_title_mode: str = Field(default="local", pattern="^(off|local|model)$")
    title_provider_id: str = ""
    summary_enabled: bool = True
    summary_trigger_rounds: int = Field(default=24, ge=4, le=200)
    summary_prompt: str = Field(default=DEFAULT_SUMMARY_PROMPT, min_length=20, max_length=10000)
    display_name: str = Field(default="", max_length=40)
    proactive_questions: bool = False
    tool_permissions: dict[str, str] = Field(default_factory=lambda: {
        "web_search": "allow", "file_read": "allow", "memory_read": "allow", "memory_write": "ask",
        "diary_write": "ask", "delete": "ask"
    })
    font_scale: int = Field(default=100, ge=85, le=130)
    message_density: str = Field(default="comfortable", pattern="^(compact|comfortable|relaxed)$")
    code_theme: str = Field(default="auto", pattern="^(auto|light|dark|contrast)$")
    memory_strategy: str = Field(default="hybrid", pattern="^(local_first|hybrid|remote_first)$")
    stream_speed: str = Field(default="standard", pattern="^(slow|standard|fast)$")


class MemoryIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=20000)
    kind: str = Field(default="fact", pattern="^(fact|preference|relationship|promise|event|emotion|summary|diary|other)$")
    source_conversation_id: str | None = None
    source_message_id: str | None = None


class MemoryState(BaseModel):
    starred: bool | None = None
    archived: bool | None = None
    trash: bool | None = None


class HealthSyncEnvelope(BaseModel):
    device_id: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    nonce: str = Field(min_length=16, max_length=64)
    ciphertext: str = Field(min_length=24, max_length=200000)


class JournalIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=30000)
    space: str = Field(default="user", pattern="^(user|shared|ai)$")
    author: str = Field(default="user", pattern="^(user|ai)$")
    visible_to_user: bool = True
    visible_to_ai: bool = False


class BoardMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    author: str = Field(default="user", pattern="^(user|ai)$")
    visible_to_user: bool = True
    visible_to_ai: bool = True


class ChatIn(BaseModel):
    conversation_id: str
    content: str = Field(min_length=1)
    provider_id: str
    persona_id: str | None = None
    reuse_user_message_id: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    local_time: str = Field(default="", max_length=80)
    game_context: str = Field(default="", max_length=2400)
    media_context: str = Field(default="", max_length=16000)
    worldbook_ids: list[str] = Field(default_factory=list, max_length=50)


class MotivationEventIn(BaseModel):
    event: str


class MotivationEnabledIn(BaseModel):
    enabled: bool
    offline_mode: str = Field(default="limited", pattern="^(limited|realtime|frozen)$")


class GameActionIn(BaseModel):
    action: str
    amount: int = Field(default=1, ge=1, le=20)
    target: str = ""


class AiGameTurnIn(BaseModel):
    provider_id: str
    persona_id: str | None = None
    turns: int = Field(default=1, ge=1, le=3)
    max_spend: int = Field(default=30, ge=0, le=100)


class FavoriteIn(BaseModel):
    owner: str = Field(default="user", pattern="^(user|assistant)$")


class RoleplayCastIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    persona_id: str | None = None
    provider_id: str
    description: str = Field(default="", max_length=6000)


class RoleplayStoryIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    player_name: str = Field(min_length=1, max_length=80)
    premise: str = Field(default="", max_length=30000)
    narrator_provider_id: str
    cast: list[RoleplayCastIn] = Field(default_factory=list, min_length=1, max_length=12)


class RoleplayTurnIn(BaseModel):
    player_input: str = Field(min_length=1, max_length=30000)


class RoleplayStateIn(BaseModel):
    status: str = Field(pattern="^(active|completed)$")


app = FastAPI(title="Local Claude Style Client", docs_url=None, redoc_url=None)


@app.on_event("startup")
def startup() -> None:
    init_db()


def masked_provider(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["prompt_cache"] = bool(item["prompt_cache"])
    item["thinking_enabled"] = bool(item["thinking_enabled"])
    item["stream_enabled"] = bool(item["stream_enabled"])
    item["has_api_key"] = bool(item.pop("api_key"))
    return item


def worldbook_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row);item["enabled"] = bool(item["enabled"])
    try: item["entries"] = json.loads(item.pop("entries_json"))
    except json.JSONDecodeError: item["entries"] = []
    return item


def masked_mcp_server(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item.pop("token", None)
    item["has_token"] = bool(row["token"])
    for source, target, fallback in (("args_json", "args", []), ("env_json", "env", {}), ("headers_json", "headers", {}), ("tools_json", "tools", []), ("tool_policy_json", "tool_policies", {})):
        try:
            item[target] = json.loads(item.pop(source))
        except (json.JSONDecodeError, TypeError):
            item[target] = fallback
    item["env_keys"] = sorted(item.pop("env").keys())
    item["headers"] = {key: ("••••" if key.lower() in ("authorization", "x-api-key") else value) for key, value in item["headers"].items()}
    return item


PERSONA_CONFIG_DEFAULTS = {
    "memory_enabled": True, "history_enabled": True, "summary_frequency": 20,
    "quick_phrases": [], "custom_headers": {}, "custom_body": {}, "regex_rules": [],
    "tools": {"time": True, "clipboard": False, "tts": False, "ask_user": True, "calculator": True},
    "mcp_servers": [], "provider_id": "", "stream_enabled": None, "startup_chat": "resume",
}


def normalize_persona_config(value: Any) -> dict[str, Any]:
    config = dict(PERSONA_CONFIG_DEFAULTS)
    config["tools"] = dict(PERSONA_CONFIG_DEFAULTS["tools"])
    if isinstance(value, str):
        try: value = json.loads(value)
        except json.JSONDecodeError: value = {}
    if isinstance(value, dict):
        config.update({key: item for key, item in value.items() if key in config})
        if isinstance(value.get("tools"), dict): config["tools"].update(value["tools"])
    config["summary_frequency"] = max(1, min(200, int(config.get("summary_frequency") or 20)))
    for key in ("quick_phrases", "regex_rules", "mcp_servers"):
        if not isinstance(config.get(key), list): config[key] = []
    for key in ("custom_headers", "custom_body"):
        if not isinstance(config.get(key), dict): config[key] = {}
    if config.get("startup_chat") not in ("resume", "new"): config["startup_chat"] = "resume"
    return config


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    with closing(db()) as connection:
        providers = [masked_provider(row) for row in connection.execute("SELECT * FROM providers ORDER BY created_at")]
        personas = [{**dict(row), "config": normalize_persona_config(row["config_json"])} for row in connection.execute("SELECT p.*,c.config_json FROM personas p LEFT JOIN persona_configs c ON c.persona_id=p.id ORDER BY p.created_at")]
        conversations = [dict(row) for row in connection.execute("SELECT * FROM conversations ORDER BY updated_at DESC")]
        worldbooks = [worldbook_dict(row) for row in connection.execute("SELECT * FROM worldbooks ORDER BY updated_at DESC")]
        mcp_servers = [masked_mcp_server(row) for row in connection.execute("SELECT * FROM mcp_servers ORDER BY updated_at DESC")]
        settings_rows = {row["key"]: row["value"] for row in connection.execute("SELECT * FROM app_settings")}
    return {"providers": providers, "personas": personas, "conversations": conversations, "worldbooks": worldbooks, "mcp_servers": mcp_servers, "settings": {
        "auto_title_mode": settings_rows.get("auto_title_mode", "local"),
        "title_provider_id": settings_rows.get("title_provider_id", ""),
        "summary_enabled": settings_rows.get("summary_enabled", "true") == "true",
        "summary_trigger_rounds": int(settings_rows.get("summary_trigger_rounds", "24")),
        "summary_prompt": settings_rows.get("summary_prompt", DEFAULT_SUMMARY_PROMPT),
        "default_summary_prompt": DEFAULT_SUMMARY_PROMPT,
        "display_name": settings_rows.get("display_name", ""),
        "proactive_questions": settings_rows.get("proactive_questions", "false") == "true",
        "tool_permissions": json.loads(settings_rows.get("tool_permissions", '{"web_search":"allow","file_read":"allow","memory_read":"allow","memory_write":"ask","diary_write":"ask","delete":"ask"}')),
        "font_scale": int(settings_rows.get("font_scale", "100")),
        "message_density": settings_rows.get("message_density", "comfortable"),
        "code_theme": settings_rows.get("code_theme", "auto"),
        "memory_strategy": settings_rows.get("memory_strategy", "hybrid"),
        "stream_speed": settings_rows.get("stream_speed", "standard"),
    }}


@app.put("/api/settings")
def save_settings(body: AppSettingsIn) -> dict[str, Any]:
    with closing(db()) as connection:
        values = {
            "auto_title_mode": body.auto_title_mode,
            "title_provider_id": body.title_provider_id,
            "summary_enabled": "true" if body.summary_enabled else "false",
            "summary_trigger_rounds": str(body.summary_trigger_rounds),
            "summary_prompt": body.summary_prompt,
            "display_name": body.display_name,
            "proactive_questions": "true" if body.proactive_questions else "false",
            "tool_permissions": json.dumps(body.tool_permissions, ensure_ascii=False),
            "font_scale": str(body.font_scale),
            "message_density": body.message_density,
            "code_theme": body.code_theme,
            "memory_strategy": body.memory_strategy,
            "stream_speed": body.stream_speed,
        }
        connection.executemany(
            "INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            values.items(),
        )
        connection.commit()
    return body.model_dump()


def memory_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["starred"] = bool(item["starred"])
    item["archived"] = bool(item["archived"])
    item["trashed"] = bool(item["deleted_at"])
    return item


@app.get("/api/memories")
def list_memories(q: str = "", include_archived: bool = False, include_trash: bool = False) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if not include_archived:
        clauses.append("archived=0")
    clauses.append("deleted_at IS NOT NULL" if include_trash else "deleted_at IS NULL")
    if q.strip():
        clauses.append("(title LIKE ? OR content LIKE ?)")
        params.extend([f"%{q.strip()}%", f"%{q.strip()}%"])
    where = " AND ".join(clauses) or "1=1"
    with closing(db()) as connection:
        rows = connection.execute(f"SELECT * FROM memories WHERE {where} ORDER BY starred DESC, updated_at DESC", params).fetchall()
    return [memory_dict(row) for row in rows]


@app.post("/api/memories")
def create_memory(body: MemoryIn) -> dict[str, Any]:
    memory_id = str(uuid.uuid4())
    created = now_iso()
    with closing(db()) as connection:
        connection.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, 0, 0, NULL, ?, ?)",
            (memory_id, body.title, body.content, body.kind, body.source_conversation_id, body.source_message_id, created, created),
        )
        connection.execute("INSERT INTO memory_audit VALUES (?, ?, 'create', '', ?)", (str(uuid.uuid4()), memory_id, created))
        connection.commit()
        row = connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
    return memory_dict(row)


@app.put("/api/memories/{memory_id}")
def update_memory(memory_id: str, body: MemoryIn) -> dict[str, Any]:
    updated = now_iso()
    with closing(db()) as connection:
        cursor = connection.execute(
            "UPDATE memories SET title=?,content=?,kind=?,updated_at=? WHERE id=? AND deleted_at IS NULL",
            (body.title, body.content, body.kind, updated, memory_id),
        )
        if not cursor.rowcount:
            raise HTTPException(404, "记忆不存在")
        connection.execute("INSERT INTO memory_audit VALUES (?, ?, 'edit', '', ?)", (str(uuid.uuid4()), memory_id, updated))
        connection.commit()
        row = connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
    return memory_dict(row)


@app.patch("/api/memories/{memory_id}/state")
def update_memory_state(memory_id: str, body: MemoryState) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if body.starred is not None:
        updates["starred"] = int(body.starred)
    if body.archived is not None:
        updates["archived"] = int(body.archived)
    if body.trash is not None:
        updates["deleted_at"] = now_iso() if body.trash else None
    if not updates:
        raise HTTPException(400, "没有需要更新的状态")
    with closing(db()) as connection:
        assignments = ", ".join(f"{key}=?" for key in updates)
        cursor = connection.execute(f"UPDATE memories SET {assignments}, updated_at=? WHERE id=?", (*updates.values(), now_iso(), memory_id))
        if not cursor.rowcount:
            raise HTTPException(404, "记忆不存在")
        connection.execute("INSERT INTO memory_audit VALUES (?, ?, 'state', ?, ?)", (str(uuid.uuid4()), memory_id, json.dumps(updates, ensure_ascii=False), now_iso()))
        connection.commit()
        row = connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
    return memory_dict(row)


@app.get("/api/journals/{persona_key}")
def list_journals(persona_key: str) -> dict[str, Any]:
    with closing(db()) as connection:
        rows = connection.execute(
            "SELECT * FROM journal_entries WHERE persona_key=? AND visible_to_user=1 ORDER BY updated_at DESC",
            (persona_key,),
        ).fetchall()
        sealed = connection.execute(
            "SELECT COUNT(*) count FROM journal_entries WHERE persona_key=? AND visible_to_user=0",
            (persona_key,),
        ).fetchone()["count"]
    return {"entries": [dict(row) for row in rows], "sealed_count": sealed}


@app.post("/api/journals/{persona_key}")
def create_journal(persona_key: str, body: JournalIn) -> dict[str, Any]:
    entry_id, created = str(uuid.uuid4()), now_iso()
    with closing(db()) as connection:
        connection.execute(
            "INSERT INTO journal_entries VALUES(?,?,?,?,?,?,?,?,?,?)",
            (entry_id, persona_key, body.title, body.content, body.space, body.author,
             int(body.visible_to_user), int(body.visible_to_ai), created, created),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
    return dict(row)


@app.put("/api/journals/{persona_key}/{entry_id}")
def update_journal(persona_key: str, entry_id: str, body: JournalIn) -> dict[str, Any]:
    with closing(db()) as connection:
        cursor = connection.execute(
            "UPDATE journal_entries SET title=?,content=?,space=?,author=?,visible_to_user=?,visible_to_ai=?,updated_at=? "
            "WHERE id=? AND persona_key=?",
            (body.title, body.content, body.space, body.author, int(body.visible_to_user),
             int(body.visible_to_ai), now_iso(), entry_id, persona_key),
        )
        if not cursor.rowcount:
            raise HTTPException(404, "日记不存在")
        connection.commit()
        row = connection.execute("SELECT * FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
    return dict(row)


@app.delete("/api/journals/{persona_key}/{entry_id}")
def delete_journal(persona_key: str, entry_id: str) -> dict[str, bool]:
    with closing(db()) as connection:
        cursor = connection.execute("DELETE FROM journal_entries WHERE id=? AND persona_key=?", (entry_id, persona_key))
        if not cursor.rowcount:
            raise HTTPException(404, "日记不存在")
        connection.commit()
    return {"ok": True}


@app.get("/api/board/{persona_key}")
def list_board_messages(persona_key: str) -> dict[str, Any]:
    with closing(db()) as connection:
        rows = connection.execute(
            "SELECT * FROM board_messages WHERE persona_key=? AND visible_to_user=1 ORDER BY created_at DESC LIMIT 200",
            (persona_key,),
        ).fetchall()
        sealed = connection.execute(
            "SELECT COUNT(*) count FROM board_messages WHERE persona_key=? AND visible_to_user=0",
            (persona_key,),
        ).fetchone()["count"]
    return {"messages": [dict(row) for row in rows], "sealed_count": sealed}


@app.post("/api/board/{persona_key}")
def create_board_message(persona_key: str, body: BoardMessageIn) -> dict[str, Any]:
    message_id, created = str(uuid.uuid4()), now_iso()
    with closing(db()) as connection:
        connection.execute(
            "INSERT INTO board_messages VALUES(?,?,?,?,?,?,?)",
            (message_id, persona_key, body.content, body.author, int(body.visible_to_user), int(body.visible_to_ai), created),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM board_messages WHERE id=?", (message_id,)).fetchone()
    return dict(row)


@app.delete("/api/board/{persona_key}/{message_id}")
def delete_board_message(persona_key: str, message_id: str) -> dict[str, bool]:
    with closing(db()) as connection:
        cursor = connection.execute("DELETE FROM board_messages WHERE id=? AND persona_key=?", (message_id, persona_key))
        if not cursor.rowcount:
            raise HTTPException(404, "留言不存在")
        connection.commit()
    return {"ok": True}


@app.post("/api/providers")
def save_provider(body: ProviderIn) -> dict[str, Any]:
    provider_id = str(uuid.uuid4())
    protocol = body.protocol
    signature = f"{body.base_url} {body.model}".lower()
    if protocol == "openai" and "deepseek" in signature:
        protocol = "deepseek"
    elif protocol == "openai" and ("bigmodel.cn" in signature or body.model.lower().startswith("glm-")):
        protocol = "glm"
    with closing(db()) as connection:
        api_key = body.api_key
        if not api_key and body.source_provider_id:
            source = connection.execute("SELECT api_key FROM providers WHERE id=?", (body.source_provider_id,)).fetchone()
            if not source:
                raise HTTPException(404, "用于复制的 API 线路不存在")
            api_key = source["api_key"]
        if not api_key:
            source = connection.execute(
                "SELECT api_key FROM providers WHERE protocol=? AND rtrim(base_url,'/')=? AND api_key<>'' ORDER BY created_at DESC LIMIT 1",
                (protocol, body.base_url.rstrip("/")),
            ).fetchone()
            if source:
                api_key = source["api_key"]
        connection.execute(
            "INSERT INTO providers(id,name,protocol,base_url,api_key,model,enabled,custom_headers,prompt_cache,thinking_enabled,stream_enabled,temperature,top_p,max_tokens,created_at,vision_mode,cache_mode,prompt_cache_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (provider_id, body.name, protocol, body.base_url.rstrip("/"), api_key, body.model, int(body.enabled), body.custom_headers, int(body.prompt_cache), int(body.thinking_enabled), int(body.stream_enabled), body.temperature, body.top_p, body.max_tokens, now_iso(), body.vision_mode, body.cache_mode, body.prompt_cache_key),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    return masked_provider(row)


@app.put("/api/providers/{provider_id}")
def update_provider(provider_id: str, body: ProviderIn) -> dict[str, Any]:
    with closing(db()) as connection:
        existing = connection.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "API 线路不存在")
        api_key = body.api_key or existing["api_key"]
        connection.execute("""UPDATE providers SET name=?,protocol=?,base_url=?,api_key=?,model=?,enabled=?,custom_headers=?,prompt_cache=?,thinking_enabled=?,stream_enabled=?,temperature=?,top_p=?,max_tokens=?,vision_mode=?,cache_mode=?,prompt_cache_key=? WHERE id=?""",
            (body.name, body.protocol, body.base_url.rstrip("/"), api_key, body.model, int(body.enabled), body.custom_headers, int(body.prompt_cache), int(body.thinking_enabled), int(body.stream_enabled), body.temperature, body.top_p, body.max_tokens, body.vision_mode, body.cache_mode, body.prompt_cache_key, provider_id))
        connection.commit()
        row = connection.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    return masked_provider(row)


@app.delete("/api/providers/{provider_id}")
def delete_provider(provider_id: str) -> dict[str, bool]:
    with closing(db()) as connection:
        connection.execute("DELETE FROM providers WHERE id=?", (provider_id,))
        connection.commit()
    return {"ok": True}


@app.post("/api/personas")
def save_persona(body: PersonaIn) -> dict[str, Any]:
    persona_id = str(uuid.uuid4())
    created = now_iso()
    with closing(db()) as connection:
        connection.execute("INSERT INTO personas VALUES (?, ?, ?, ?)", (persona_id, body.name, body.prompt, created))
        config = normalize_persona_config(body.config)
        connection.execute("INSERT INTO persona_configs VALUES (?,?,?)", (persona_id, json.dumps(config, ensure_ascii=False), created))
        connection.commit()
    return {"id": persona_id, "name": body.name, "prompt": body.prompt, "config": config, "created_at": created}


@app.put("/api/personas/{persona_id}")
def update_persona(persona_id: str, body: PersonaIn) -> dict[str, Any]:
    with closing(db()) as connection:
        existing = connection.execute("SELECT * FROM personas WHERE id=?", (persona_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "人格不存在")
        config = normalize_persona_config(body.config)
        connection.execute("UPDATE personas SET name=?,prompt=? WHERE id=?", (body.name, body.prompt, persona_id))
        connection.execute("INSERT INTO persona_configs VALUES (?,?,?) ON CONFLICT(persona_id) DO UPDATE SET config_json=excluded.config_json,updated_at=excluded.updated_at", (persona_id, json.dumps(config, ensure_ascii=False), now_iso()))
        connection.commit()
    return {"id": persona_id, "name": body.name, "prompt": body.prompt, "config": config, "created_at": existing["created_at"]}


@app.delete("/api/personas/{persona_id}")
def delete_persona(persona_id: str) -> dict[str, bool]:
    with closing(db()) as connection:
        connection.execute("UPDATE conversations SET persona_id=NULL WHERE persona_id=?", (persona_id,))
        connection.execute("DELETE FROM personas WHERE id=?", (persona_id,))
        connection.execute("DELETE FROM persona_configs WHERE persona_id=?", (persona_id,))
        connection.commit()
    return {"ok": True}


def normalize_worldbook_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for raw in entries[:500]:
        if not isinstance(raw, dict) or not str(raw.get("content", "")).strip(): continue
        normalized.append({
            "id": str(raw.get("id") or uuid.uuid4()), "name": str(raw.get("name") or "未命名条目")[:100],
            "content": str(raw.get("content") or "")[:100000], "enabled": bool(raw.get("enabled", True)),
            "constant": bool(raw.get("constant", False)), "keywords": [str(item)[:200] for item in raw.get("keywords", []) if str(item).strip()][:100],
            "use_regex": bool(raw.get("use_regex", False)), "case_sensitive": bool(raw.get("case_sensitive", False)),
            "scan_depth": max(1, min(100, int(raw.get("scan_depth") or 4))),
            "position": str(raw.get("position") or "system_after") if str(raw.get("position") or "system_after") in ("system_before", "system_after", "history_before", "history_after") else "system_after",
            "role": str(raw.get("role") or "system") if str(raw.get("role") or "system") in ("system", "user", "assistant") else "system",
            "priority": max(-9999, min(9999, int(raw.get("priority") or 0))),
        })
    return normalized


@app.post("/api/worldbooks")
def create_worldbook(body: WorldbookIn) -> dict[str, Any]:
    item_id,created=str(uuid.uuid4()),now_iso();entries=normalize_worldbook_entries(body.entries)
    with closing(db()) as connection:
        connection.execute("INSERT INTO worldbooks VALUES (?,?,?,?,?,?,?)",(item_id,body.name,body.description,int(body.enabled),json.dumps(entries,ensure_ascii=False),created,created));connection.commit()
        return worldbook_dict(connection.execute("SELECT * FROM worldbooks WHERE id=?",(item_id,)).fetchone())


@app.put("/api/worldbooks/{worldbook_id}")
def update_worldbook(worldbook_id: str, body: WorldbookIn) -> dict[str, Any]:
    entries=normalize_worldbook_entries(body.entries)
    with closing(db()) as connection:
        if not connection.execute("SELECT 1 FROM worldbooks WHERE id=?",(worldbook_id,)).fetchone(): raise HTTPException(404,"世界书不存在")
        connection.execute("UPDATE worldbooks SET name=?,description=?,enabled=?,entries_json=?,updated_at=? WHERE id=?",(body.name,body.description,int(body.enabled),json.dumps(entries,ensure_ascii=False),now_iso(),worldbook_id));connection.commit()
        return worldbook_dict(connection.execute("SELECT * FROM worldbooks WHERE id=?",(worldbook_id,)).fetchone())


@app.delete("/api/worldbooks/{worldbook_id}")
def delete_worldbook(worldbook_id: str) -> dict[str, bool]:
    with closing(db()) as connection: connection.execute("DELETE FROM worldbooks WHERE id=?",(worldbook_id,));connection.commit()
    return {"ok":True}


def mcp_headers(token: str = "", session_id: str = "") -> dict[str, str]:
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    if session_id:
        headers["mcp-session-id"] = session_id
    return headers


def parse_mcp_response(response: httpx.Response) -> dict[str, Any]:
    text = response.text.strip()
    if not text:
        return {}
    if "text/event-stream" in response.headers.get("content-type", "") or text.startswith("event:") or text.startswith("data:"):
        payloads = [line[5:].strip() for line in text.splitlines() if line.startswith("data:") and line[5:].strip()]
        if not payloads:
            return {}
        text = payloads[-1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(502, "MCP 服务返回的不是有效 JSON/SSE") from exc
    if not isinstance(payload, dict):
        raise HTTPException(502, "MCP 服务返回格式无效")
    if payload.get("error"):
        error = payload["error"]
        detail = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        raise HTTPException(502, f"MCP 错误：{detail}")
    return payload


async def mcp_post(client: httpx.AsyncClient, url: str, token: str, method: str, params: dict[str, Any] | None = None, request_id: int | None = 1, session_id: str = "") -> tuple[dict[str, Any], str]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        body["id"] = request_id
    if params is not None:
        body["params"] = params
    try:
        response = await client.post(url, headers=mcp_headers(token, session_id), json=body)
    except httpx.RequestError as exc:
        raise HTTPException(502, f"无法连接 MCP：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(response.status_code, f"MCP HTTP {response.status_code}：{response.text[:300]}")
    return parse_mcp_response(response), response.headers.get("mcp-session-id", session_id)


async def discover_mcp_tools(url: str, token: str, custom_headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise HTTPException(422, "MCP 地址必须使用 http:// 或 https://")
    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers=custom_headers or {}) as client:
        initialized, session_id = await mcp_post(client, url, token, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "Atherloom", "version": "0.5.15"},
        })
        if not initialized.get("result"):
            raise HTTPException(502, "MCP 初始化没有返回 result")
        await mcp_post(client, url, token, "notifications/initialized", request_id=None, session_id=session_id)
        listed, _ = await mcp_post(client, url, token, "tools/list", {}, 2, session_id)
    tools = listed.get("result", {}).get("tools", [])
    return tools if isinstance(tools, list) else []


async def call_mcp_tool(url: str, token: str, name: str, arguments: dict[str, Any], custom_headers: dict[str, str] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=custom_headers or {}) as client:
        _, session_id = await mcp_post(client, url, token, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "Atherloom", "version": "0.5.15"},
        })
        await mcp_post(client, url, token, "notifications/initialized", request_id=None, session_id=session_id)
        called, _ = await mcp_post(client, url, token, "tools/call", {"name": name, "arguments": arguments}, 3, session_id)
    return called.get("result", {})


async def stdio_mcp_exchange(server: dict[str, Any], method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    command = str(server.get("command", "")).strip()
    if not command:
        raise HTTPException(422, "stdio MCP 缺少启动命令")
    args = server.get("args", [])
    env = {**os.environ, **{str(key): str(value) for key, value in server.get("env", {}).items()}}
    try:
        process = await asyncio.create_subprocess_exec(command, *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
    except OSError as exc:
        raise HTTPException(502, f"无法启动 stdio MCP：{exc}") from exc
    next_id = 1
    async def request(name: str, request_params: dict[str, Any] | None = None, notification: bool = False) -> dict[str, Any]:
        nonlocal next_id
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": name}
        request_id = None if notification else next_id
        if request_id is not None:
            payload["id"] = request_id
            next_id += 1
        if request_params is not None:
            payload["params"] = request_params
        assert process.stdin and process.stdout
        process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        await process.stdin.drain()
        if notification:
            return {}
        for _ in range(100):
            line = await asyncio.wait_for(process.stdout.readline(), timeout=30)
            if not line:
                break
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") == request_id:
                if response.get("error"):
                    raise HTTPException(502, f"MCP 错误：{response['error']}")
                return response
        raise HTTPException(502, "stdio MCP 没有返回有效响应")
    try:
        await request("initialize", {"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"Atherloom","version":"0.5.15"}})
        await request("notifications/initialized", notification=True)
        return await request(method, params or {})
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()


def expanded_mcp_server(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for source, target, fallback in (("args_json","args",[]),("env_json","env",{}),("headers_json","headers",{}),("tool_policy_json","tool_policies",{})):
        if source in item:
            try: item[target] = json.loads(item[source])
            except (json.JSONDecodeError, TypeError): item[target] = fallback
    return item


async def discover_server_tools(server: dict[str, Any]) -> list[dict[str, Any]]:
    server = expanded_mcp_server(server)
    if server.get("transport") == "stdio":
        response = await stdio_mcp_exchange(server, "tools/list")
        tools = response.get("result", {}).get("tools", [])
        return tools if isinstance(tools, list) else []
    return await discover_mcp_tools(server.get("url", ""), server.get("token", ""), server.get("headers", {}))


async def invoke_server_tool(server: dict[str, Any], name: str, arguments: dict[str, Any]) -> Any:
    server = expanded_mcp_server(server)
    if server.get("transport") == "builtin":
        return await invoke_builtin_tool(name, arguments)
    if server.get("transport") == "stdio":
        response = await stdio_mcp_exchange(server, "tools/call", {"name": name, "arguments": arguments})
        return response.get("result", {})
    return await call_mcp_tool(server.get("url", ""), server.get("token", ""), name, arguments, server.get("headers", {}))


def mcp_result_text(result: Any) -> str:
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        pieces = []
        for block in result["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                pieces.append(str(block.get("text", "")))
            elif isinstance(block, dict):
                pieces.append(json.dumps(block, ensure_ascii=False))
        if pieces:
            return "\n".join(pieces)
    return json.dumps(result, ensure_ascii=False)


def parse_dsml_tool_calls(content: str) -> list[dict[str, Any]]:
    """Parse DSML calls emitted inside assistant content by any gateway."""
    marker = r"[|｜]\s*DSML\s*[|｜]"
    invoke_pattern = re.compile(
        rf"<{marker}\s*invoke\b([^>]*)>([\s\S]*?)<{marker}\s*/\s*invoke\s*>",
        re.IGNORECASE,
    )
    parameter_pattern = re.compile(
        rf"<{marker}\s*parameter\b([^>]*)>([\s\S]*?)<{marker}\s*/\s*parameter\s*>",
        re.IGNORECASE,
    )
    calls: list[dict[str, Any]] = []
    for invoke in invoke_pattern.finditer(str(content or "")):
        name_match = re.search(r"""\bname\s*=\s*["']([^"']+)["']""", invoke.group(1), re.IGNORECASE)
        if not name_match:
            continue
        arguments: dict[str, Any] = {}
        for parameter in parameter_pattern.finditer(invoke.group(2)):
            key_match = re.search(r"""\bname\s*=\s*["']([^"']+)["']""", parameter.group(1), re.IGNORECASE)
            if not key_match:
                continue
            raw_value = parameter.group(2).strip()
            is_string = not re.search(r"""\bstring\s*=\s*["']false["']""", parameter.group(1), re.IGNORECASE)
            if is_string:
                value: Any = raw_value
            else:
                try:
                    value = json.loads(raw_value)
                except (json.JSONDecodeError, TypeError):
                    value = raw_value
            arguments[key_match.group(1).strip()] = value
        calls.append(
            {
                "id": f"dsml-{uuid.uuid4()}",
                "type": "function",
                "function": {
                    "name": name_match.group(1).strip(),
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
                "_dsml": True,
            }
        )
    return calls


async def bound_mcp_catalog(servers: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, tuple[dict[str, Any], str]]]:
    catalog: list[dict[str, Any]] = []
    bindings: dict[str, tuple[dict[str, Any], str]] = {}
    for server in servers:
        try:
            tools = await discover_server_tools(server)
        except HTTPException:
            continue
        for tool in tools:
            original = str(tool.get("name", "")).strip()
            if not original:
                continue
            policy = expanded_mcp_server(server).get("tool_policies", {}).get(original, "allow")
            if policy == "deny":
                continue
            safe_base = re.sub(r"[^a-zA-Z0-9_-]", "_", original)[:40] or "tool"
            safe = f"mcp_{hashlib.sha1(server['id'].encode()).hexdigest()[:8]}_{safe_base}"[:64]
            schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object", "properties": {}}
            catalog.append({"name": safe, "description": f"[{server['name']}] {tool.get('description', '')}".strip(), "input_schema": schema})
            bindings[safe] = (server, original)
    return catalog, bindings


BUILTIN_TOOL_SPECS = {
    "web_search": {
        "permission": "web_search",
        "description": "搜索公开互联网，返回标题、链接与摘要。适合查询最新信息或核实事实。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "具体、完整的搜索关键词"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5},
            },
            "required": ["query"],
        },
    },
    "memory_search": {
        "permission": "memory_read",
        "description": "检索 Atherloom 本地长期记忆，返回可用于后续更新的 memory_id。",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "记忆标题或内容关键词；留空返回最近记忆"}},
        },
    },
    "memory_create": {
        "permission": "memory_write",
        "description": "新增一条本地长期记忆。只保存用户明确表达、值得跨对话保留的信息。",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "简短明确的标题"},
                "content": {"type": "string", "description": "忠实、完整且不臆测的记忆内容"},
                "kind": {"type": "string", "enum": ["fact", "preference", "relationship", "promise", "event", "emotion", "summary", "diary", "other"]},
                "source_message_id": {"type": "string", "description": "如果记忆来自某条具体消息，填写该消息 ID，以便回溯原话"},
            },
            "required": ["title", "content"],
        },
    },
    "memory_update": {
        "permission": "memory_write",
        "description": "按 memory_id 更新已有本地记忆。应先调用 memory_search 获得准确 ID。",
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "memory_search 返回的准确 ID"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "kind": {"type": "string", "enum": ["fact", "preference", "relationship", "promise", "event", "emotion", "summary", "diary", "other"]},
            },
            "required": ["memory_id"],
        },
    },
    "journal_create": {
        "permission": "diary_write",
        "description": "写一篇 AI 日记或共同日记。可以对用户公开，也可以作为 AI 的密封私人日记。",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"}, "content": {"type": "string"},
                "space": {"type": "string", "enum": ["shared", "ai"]},
                "visible_to_user": {"type": "boolean"},
            },
            "required": ["title", "content", "space", "visible_to_user"],
        },
    },
    "board_create": {
        "permission": "diary_write",
        "description": "在当前人格的留言板给用户留一条短消息。",
        "input_schema": {
            "type": "object",
            "properties": {"content": {"type": "string"}, "visible_to_user": {"type": "boolean"}},
            "required": ["content", "visible_to_user"],
        },
    },
}


def builtin_tool_catalog(permissions: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, tuple[dict[str, Any], str]]]:
    server = {
        "id": "__builtin__", "name": "Atherloom 内置工具", "transport": "builtin",
        "tool_policies": {name: permissions.get(spec["permission"], "ask") for name, spec in BUILTIN_TOOL_SPECS.items()},
    }
    catalog, bindings = [], {}
    for name, spec in BUILTIN_TOOL_SPECS.items():
        if permissions.get(spec["permission"], "ask") != "allow":
            continue
        safe_name = f"atherloom_{name}"
        catalog.append({"name": safe_name, "description": spec["description"], "input_schema": spec["input_schema"]})
        bindings[safe_name] = (server, name)
    return catalog, bindings


def _clean_search_text(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


async def invoke_builtin_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "web_search":
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("搜索关键词不能为空")
        limit = max(1, min(int(arguments.get("max_results") or 5), 8))
        async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 Atherloom/0.5"}) as client:
            response = await client.get("https://html.duckduckgo.com/html/", params={"q": query})
            response.raise_for_status()
        links = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', response.text, re.I | re.S)
        snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>|<div[^>]+class="result__snippet"[^>]*>(.*?)</div>', response.text, re.I | re.S)
        results = []
        for index, (raw_url, title) in enumerate(links[:limit]):
            parsed = urlparse(html_lib.unescape(raw_url))
            actual_url = unquote(parse_qs(parsed.query).get("uddg", [raw_url])[0])
            snippet_parts = snippets[index] if index < len(snippets) else ("", "")
            results.append({"title": _clean_search_text(title), "url": actual_url, "snippet": _clean_search_text(snippet_parts[0] or snippet_parts[1])})
        return {"query": query, "results": results, "result_count": len(results)}
    if name == "memory_search":
        query = str(arguments.get("query", "")).strip()
        with closing(db()) as connection:
            if query:
                rows = connection.execute(
                    "SELECT * FROM memories WHERE deleted_at IS NULL AND (title LIKE ? OR content LIKE ?) ORDER BY starred DESC,updated_at DESC LIMIT 20",
                    (f"%{query}%", f"%{query}%"),
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM memories WHERE deleted_at IS NULL ORDER BY starred DESC,updated_at DESC LIMIT 20").fetchall()
        return {"memories": [{"memory_id": row["id"], "title": row["title"], "content": row["content"], "kind": row["kind"], "updated_at": row["updated_at"]} for row in rows]}
    if name == "memory_create":
        source_message_id = str(arguments.get("source_message_id") or arguments.get("_source_message_id") or "").strip() or None
        source_conversation_id = str(arguments.get("_conversation_id") or "").strip() or None
        if source_message_id:
            with closing(db()) as connection:
                source = connection.execute("SELECT conversation_id FROM messages WHERE id=?", (source_message_id,)).fetchone()
            if not source:
                raise ValueError("source_message_id 找不到对应消息")
            source_conversation_id = source["conversation_id"]
        body = MemoryIn(
            title=str(arguments.get("title", "")).strip(),
            content=str(arguments.get("content", "")).strip(),
            kind=str(arguments.get("kind") or "fact"),
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
        )
        saved = create_memory(body)
        return {"created": True, "memory_id": saved["id"], "title": saved["title"], "kind": saved["kind"]}
    if name == "memory_update":
        memory_id = str(arguments.get("memory_id", "")).strip()
        with closing(db()) as connection:
            row = connection.execute("SELECT * FROM memories WHERE id=? AND deleted_at IS NULL", (memory_id,)).fetchone()
        if not row:
            raise ValueError("找不到该 memory_id；请先调用 memory_search")
        body = MemoryIn(
            title=str(arguments.get("title", row["title"])).strip(),
            content=str(arguments.get("content", row["content"])).strip(),
            kind=str(arguments.get("kind", row["kind"])),
            source_conversation_id=row["source_conversation_id"],
            source_message_id=row["source_message_id"],
        )
        saved = update_memory(memory_id, body)
        return {"updated": True, "memory_id": saved["id"], "title": saved["title"], "kind": saved["kind"]}
    if name == "journal_create":
        persona_key = str(arguments.get("_persona_key") or "__default__")
        saved = create_journal(persona_key, JournalIn(
            title=str(arguments.get("title", "")).strip(),
            content=str(arguments.get("content", "")).strip(),
            space=str(arguments.get("space") or "ai"),
            author="ai", visible_to_user=bool(arguments.get("visible_to_user")), visible_to_ai=True,
        ))
        return {"created": True, "journal_id": saved["id"], "sealed": not bool(saved["visible_to_user"])}
    if name == "board_create":
        persona_key = str(arguments.get("_persona_key") or "__default__")
        saved = create_board_message(persona_key, BoardMessageIn(
            content=str(arguments.get("content", "")).strip(), author="ai",
            visible_to_user=bool(arguments.get("visible_to_user")), visible_to_ai=True,
        ))
        return {"created": True, "message_id": saved["id"], "sealed": not bool(saved["visible_to_user"])}
    raise ValueError(f"未知内置工具：{name}")


@app.post("/api/mcp-servers/test")
async def test_mcp_server(body: McpServerIn) -> dict[str, Any]:
    tools = await discover_server_tools({"transport":body.transport,"url":body.url,"token":body.token,"command":body.command,"args":body.args,"env":body.env,"headers":body.headers})
    return {"ok": True, "tool_count": len(tools), "tools": [{"name": str(tool.get("name", "")), "description": str(tool.get("description", ""))} for tool in tools], "message": f"连接成功，发现 {len(tools)} 个工具"}


@app.post("/api/mcp-servers")
def create_mcp_server(body: McpServerIn) -> dict[str, Any]:
    item_id, created = str(uuid.uuid4()), now_iso()
    with closing(db()) as connection:
        try:
            connection.execute("""INSERT INTO mcp_servers
              (id,name,url,token,enabled,last_status,last_detail,last_tested_at,created_at,updated_at,transport,command,args_json,env_json,headers_json,tools_json,tool_policy_json)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (item_id,body.name,body.url,body.token,int(body.enabled),"","",None,created,created,body.transport,body.command,json.dumps(body.args,ensure_ascii=False),json.dumps(body.env,ensure_ascii=False),json.dumps(body.headers,ensure_ascii=False),"[]",json.dumps(body.tool_policies,ensure_ascii=False)))
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "MCP 服务名称已存在") from exc
        return masked_mcp_server(connection.execute("SELECT * FROM mcp_servers WHERE id=?", (item_id,)).fetchone())


@app.put("/api/mcp-servers/{server_id}")
def update_mcp_server(server_id: str, body: McpServerIn) -> dict[str, Any]:
    with closing(db()) as connection:
        existing = connection.execute("SELECT * FROM mcp_servers WHERE id=?", (server_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "MCP 服务不存在")
        token = body.token or existing["token"]
        env = body.env or json.loads(existing["env_json"] or "{}")
        existing_headers = json.loads(existing["headers_json"] or "{}")
        headers = body.headers or existing_headers
        headers = {key: (existing_headers.get(key, value) if value == "••••" else value) for key, value in headers.items()}
        try:
            connection.execute("""UPDATE mcp_servers SET name=?,url=?,token=?,enabled=?,updated_at=?,transport=?,command=?,args_json=?,env_json=?,headers_json=?,tool_policy_json=? WHERE id=?""",
              (body.name,body.url,token,int(body.enabled),now_iso(),body.transport,body.command,json.dumps(body.args,ensure_ascii=False),json.dumps(env,ensure_ascii=False),json.dumps(headers,ensure_ascii=False),json.dumps(body.tool_policies,ensure_ascii=False),server_id))
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "MCP 服务名称已存在") from exc
        return masked_mcp_server(connection.execute("SELECT * FROM mcp_servers WHERE id=?", (server_id,)).fetchone())


@app.delete("/api/mcp-servers/{server_id}")
def delete_mcp_server(server_id: str) -> dict[str, bool]:
    with closing(db()) as connection:
        connection.execute("DELETE FROM mcp_servers WHERE id=?", (server_id,))
        connection.commit()
    return {"ok": True}


@app.post("/api/mcp-servers/{server_id}/refresh")
async def refresh_mcp_server(server_id: str) -> dict[str, Any]:
    with closing(db()) as connection:
        row = connection.execute("SELECT * FROM mcp_servers WHERE id=?", (server_id,)).fetchone()
    if not row:
        raise HTTPException(404, "MCP 服务不存在")
    tested = now_iso()
    try:
        tools = await discover_server_tools(dict(row))
        status, detail = "online", f"发现 {len(tools)} 个工具"
    except Exception as exc:
        tools, status, detail = [], "error", str(exc)
    with closing(db()) as connection:
        connection.execute("UPDATE mcp_servers SET tools_json=?,last_status=?,last_detail=?,last_tested_at=?,updated_at=? WHERE id=?", (json.dumps(tools,ensure_ascii=False),status,detail,tested,tested,server_id))
        connection.commit()
        saved = masked_mcp_server(connection.execute("SELECT * FROM mcp_servers WHERE id=?", (server_id,)).fetchone())
    if status == "error":
        raise HTTPException(502, detail)
    return saved


@app.get("/api/mcp-audit")
def list_mcp_audit(limit: int = 100) -> list[dict[str, Any]]:
    with closing(db()) as connection:
        return [dict(row) for row in connection.execute(
            """SELECT a.*,
                      COALESCE(s.name, CASE WHEN a.server_id='__builtin__' THEN 'Atherloom 内置工具' ELSE NULL END) server_name,
                      c.title conversation_title,
                      m.content user_message_content
               FROM mcp_audit a
               LEFT JOIN mcp_servers s ON s.id=a.server_id
               LEFT JOIN conversations c ON c.id=a.conversation_id
               LEFT JOIN messages m ON m.id=a.user_message_id
               ORDER BY a.created_at DESC LIMIT ?""",
            (max(1, min(limit, 500)),),
        )]


def record_mcp_audit(
    server_id: str,
    tool_name: str,
    status: str,
    detail: str = "",
    conversation_id: str | None = None,
    user_message_id: str | None = None,
) -> None:
    with closing(db()) as connection:
        connection.execute(
            """INSERT INTO mcp_audit
               (id,server_id,tool_name,status,detail,conversation_id,user_message_id,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), server_id, tool_name, status, detail[:2000], conversation_id, user_message_id, now_iso()),
        )
        connection.commit()


@app.get("/api/health/status")
def health_status() -> dict[str, Any]:
    with closing(db()) as connection:
        row = connection.execute(
            "SELECT MAX(updated_at) last_sync,COUNT(*) record_count,COUNT(DISTINCT device_id) device_count FROM health_daily_summaries"
        ).fetchone()
    return {
        "enabled": health_enabled(),
        "last_sync": row["last_sync"],
        "record_count": row["record_count"],
        "device_count": row["device_count"],
        "diagnostic": False,
    }


@app.post("/api/health/sync")
def sync_health_summary(body: HealthSyncEnvelope) -> dict[str, Any]:
    try:
        payload = normalize_health_payload(
            decrypt_sync_envelope(body.device_id, body.day, body.nonce, body.ciphertext)
        )
        nonce, ciphertext = encrypt_for_storage(body.device_id, body.day, payload)
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error
    except (InvalidTag, binascii.Error, ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
        with closing(db()) as connection:
            connection.execute(
                "INSERT INTO health_sync_audit VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), body.device_id, body.day, "rejected", "invalid encrypted envelope", now_iso()),
            )
            connection.commit()
        raise HTTPException(401, "健康摘要认证失败") from error
    updated = now_iso()
    with closing(db()) as connection:
        connection.execute(
            """INSERT INTO health_daily_summaries(id,device_id,day,nonce,ciphertext,updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(device_id,day) DO UPDATE SET
               nonce=excluded.nonce,ciphertext=excluded.ciphertext,updated_at=excluded.updated_at""",
            (str(uuid.uuid4()), body.device_id, body.day, nonce, ciphertext, updated),
        )
        connection.execute(
            "INSERT INTO health_sync_audit VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), body.device_id, body.day, "success", "", updated),
        )
        connection.commit()
    return {"ok": True, "day": body.day, "updated_at": updated}


@app.get("/api/health/summaries")
def list_health_summaries(days: int = 30) -> list[dict[str, Any]]:
    if not health_enabled():
        return []
    with closing(db()) as connection:
        return load_health_summaries(connection, max(1, min(days, 365)))


@app.post("/api/conversations")
def create_conversation(body: ConversationIn) -> dict[str, Any]:
    conversation_id = str(uuid.uuid4())
    created = now_iso()
    with closing(db()) as connection:
        connection.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, '', ?, ?, 0, 0, 0)",
            (conversation_id, body.title, body.provider_id, body.persona_id, created, created),
        )
        connection.commit()
    return {"id": conversation_id, "title": body.title, "provider_id": body.provider_id, "persona_id": body.persona_id, "summary": "", "created_at": created, "updated_at": created, "pinned": 0, "starred": 0, "archived": 0}


@app.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, body: ConversationRename) -> dict[str, str]:
    title = body.title.strip()
    with closing(db()) as connection:
        cursor = connection.execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?", (title, now_iso(), conversation_id))
        connection.commit()
    if not cursor.rowcount:
        raise HTTPException(404, "会话不存在")
    return {"id": conversation_id, "title": title}


@app.patch("/api/conversations/{conversation_id}/state")
def update_conversation_state(conversation_id: str, body: ConversationState) -> dict[str, Any]:
    updates = {key: int(value) for key, value in body.model_dump().items() if value is not None}
    if not updates:
        raise HTTPException(400, "没有需要更新的状态")
    assignments = ", ".join(f"{key}=?" for key in updates)
    with closing(db()) as connection:
        cursor = connection.execute(f"UPDATE conversations SET {assignments}, updated_at=? WHERE id=?", (*updates.values(), now_iso(), conversation_id))
        connection.commit()
        row = connection.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    if not cursor.rowcount:
        raise HTTPException(404, "会话不存在")
    return dict(row)


@app.get("/api/search")
def search_conversations(q: str = "") -> list[dict[str, Any]]:
    term = q.strip()
    if not term:
        return []
    like = f"%{term}%"
    with closing(db()) as connection:
        rows = connection.execute(
            """SELECT DISTINCT c.* FROM conversations c
               LEFT JOIN messages m ON m.conversation_id=c.id
               WHERE c.title LIKE ? OR m.content LIKE ?
               ORDER BY c.updated_at DESC LIMIT 50""", (like, like)
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/messages/search")
def search_messages(q: str = "", role: str = "", limit: int = 100) -> list[dict[str, Any]]:
    term = q.strip()
    normalized_role = role.strip().lower()
    if not term:
        return []
    if normalized_role and normalized_role not in {"user", "assistant", "system"}:
        raise HTTPException(422, "role 只能是 user、assistant 或 system")
    conditions = ["m.content LIKE ?", "t.message_id IS NULL"]
    parameters: list[Any] = [f"%{term}%"]
    if normalized_role:
        conditions.append("m.role=?")
        parameters.append(normalized_role)
    parameters.append(max(1, min(limit, 500)))
    with closing(db()) as connection:
        rows = connection.execute(
            f"""SELECT m.*, c.title conversation_title
                FROM messages m
                JOIN conversations c ON c.id=m.conversation_id
                LEFT JOIN message_trash t ON t.message_id=m.id
                WHERE {' AND '.join(conditions)}
                ORDER BY m.created_at DESC, m.id DESC
                LIMIT ?""",
            parameters,
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/conversations/{conversation_id}/branch/{message_id}")
def branch_conversation(conversation_id: str, message_id: str) -> dict[str, Any]:
    with closing(db()) as connection:
        source = connection.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        pivot = connection.execute("SELECT * FROM messages WHERE id=? AND conversation_id=? AND NOT EXISTS (SELECT 1 FROM message_trash t WHERE t.message_id=messages.id)", (message_id, conversation_id)).fetchone()
        if not source or not pivot:
            raise HTTPException(404, "找不到要分支的消息")
        new_id = str(uuid.uuid4())
        created = now_iso()
        title = f"{source['title']} · 分支"
        connection.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0)",
            (new_id, title, source["provider_id"], source["persona_id"], source["summary"], created, created),
        )
        rows = connection.execute(
            "SELECT * FROM messages WHERE conversation_id=? AND created_at<=? AND NOT EXISTS (SELECT 1 FROM message_trash t WHERE t.message_id=messages.id) ORDER BY created_at", (conversation_id, pivot["created_at"])
        ).fetchall()
        for row in rows:
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), new_id, row["role"], row["content"], row["provider_id"], row["model"], row["created_at"], row["reasoning"], row["parent_message_id"]),
            )
        connection.commit()
    return {"id": new_id, "title": title, "provider_id": source["provider_id"], "persona_id": source["persona_id"], "summary": source["summary"], "created_at": created, "updated_at": created, "pinned": 0, "starred": 0, "archived": 0}


@app.get("/api/conversations/{conversation_id}/messages")
def get_messages(conversation_id: str) -> list[dict[str, Any]]:
    with closing(db()) as connection:
        return [dict(row) for row in connection.execute("""SELECT messages.*,
            CASE WHEN s.assistant_message_id=messages.id THEN 1 ELSE 0 END AS selected
            FROM messages LEFT JOIN message_selections s ON s.conversation_id=messages.conversation_id AND s.parent_message_id=messages.parent_message_id
            WHERE messages.conversation_id=? AND NOT EXISTS (SELECT 1 FROM message_trash t WHERE t.message_id=messages.id) ORDER BY messages.created_at""", (conversation_id,))]


@app.patch("/api/messages/selection")
def select_message_version(body: MessageSelectionIn) -> dict[str, Any]:
    with closing(db()) as connection:
        valid = connection.execute("SELECT 1 FROM messages WHERE id=? AND conversation_id=? AND parent_message_id=? AND role='assistant'", (body.assistant_message_id, body.conversation_id, body.parent_message_id)).fetchone()
        if not valid:
            raise HTTPException(404, "回答版本不存在")
        connection.execute("INSERT OR REPLACE INTO message_selections VALUES (?,?,?)", (body.conversation_id, body.parent_message_id, body.assistant_message_id))
        connection.commit()
    return {"ok": True}


@app.delete("/api/messages/{message_id}")
def delete_message_version(message_id: str) -> dict[str, Any]:
    with closing(db()) as connection:
        message = connection.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not message:
            raise HTTPException(404, "消息不存在")
        targets = [message_id]
        if message["role"] == "user":
            targets.extend(row["id"] for row in connection.execute("SELECT id FROM messages WHERE parent_message_id=?", (message_id,)))
        connection.executemany("INSERT OR REPLACE INTO message_trash(message_id,deleted_at) VALUES (?,?)", [(target, now_iso()) for target in targets])
        placeholders = ",".join("?" for _ in targets)
        connection.execute(f"DELETE FROM message_selections WHERE assistant_message_id IN ({placeholders})", targets)
        connection.commit()
    return {"ok": True, "deleted": targets}


@app.patch("/api/messages/{message_id}")
def edit_message(message_id: str, body: MessageEditIn) -> dict[str, Any]:
    with closing(db()) as connection:
        message = connection.execute("SELECT * FROM messages WHERE id=? AND NOT EXISTS (SELECT 1 FROM message_trash t WHERE t.message_id=messages.id)", (message_id,)).fetchone()
        if not message:
            raise HTTPException(404, "消息不存在")
        connection.execute("UPDATE messages SET content=? WHERE id=?", (body.content.strip(), message_id))
        connection.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now_iso(), message["conversation_id"]))
        connection.commit()
    return {**dict(message), "content": body.content.strip()}


@app.delete("/api/messages/{message_id}/versions")
def delete_all_message_versions(message_id: str) -> dict[str, Any]:
    with closing(db()) as connection:
        message = connection.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not message:
            raise HTTPException(404, "消息不存在")
        parent_id = message["parent_message_id"] if message["role"] == "assistant" else message_id
        if message["role"] == "assistant":
            targets = [row["id"] for row in connection.execute("SELECT id FROM messages WHERE parent_message_id=?", (parent_id,))]
        else:
            targets = [message_id, *[row["id"] for row in connection.execute("SELECT id FROM messages WHERE parent_message_id=?", (message_id,))]]
        connection.executemany("INSERT OR REPLACE INTO message_trash(message_id,deleted_at) VALUES (?,?)", [(target, now_iso()) for target in targets])
        connection.execute("DELETE FROM message_selections WHERE conversation_id=? AND parent_message_id=?", (message["conversation_id"], parent_id))
        connection.commit()
    return {"ok": True, "deleted": targets, "parent_message_id": parent_id}


@app.get("/api/favorites")
def list_favorites(q: str = "") -> list[dict[str, Any]]:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    with closing(db()) as connection:
        rows = connection.execute(
            """SELECT f.*, GROUP_CONCAT(o.owner) AS owners FROM favorites f
               LEFT JOIN favorite_owners o ON o.favorite_id=f.id
               WHERE (?='' OR f.text_snapshot LIKE ? ESCAPE '\\' OR f.conversation_title_snapshot LIKE ? ESCAPE '\\')
               GROUP BY f.id ORDER BY f.favorited_at DESC, f.id DESC LIMIT 500""",
            (q, pattern, pattern),
        ).fetchall()
    return [{**dict(row), "owners": (row["owners"] or "").split(",") if row["owners"] else []} for row in rows]


@app.post("/api/favorites/{message_id}")
def favorite_message(message_id: str, body: FavoriteIn) -> dict[str, Any]:
    with closing(db()) as connection:
        message = connection.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not message or message["role"] not in ("user", "assistant") or not message["content"].strip():
            raise HTTPException(404, "该消息不可珍藏")
        existing = connection.execute("SELECT * FROM favorites WHERE source_message_id=?", (message_id,)).fetchone()
        favorite_id = existing["id"] if existing else str(uuid.uuid4())
        if not existing:
            title = connection.execute("SELECT title FROM conversations WHERE id=?", (message["conversation_id"],)).fetchone()
            connection.execute(
                "INSERT INTO favorites VALUES(?,?,?,?,?,?,?,?)",
                (favorite_id, message_id, message["conversation_id"], message["role"], message["content"][:50000], title["title"] if title else "", message["created_at"], now_iso()),
            )
        connection.execute("INSERT OR IGNORE INTO favorite_owners VALUES(?,?,?)", (favorite_id, body.owner, now_iso()))
        connection.commit()
    return {"id": favorite_id, "source_message_id": message_id, "owner": body.owner}


@app.delete("/api/favorites/{message_id}")
def unfavorite_message(message_id: str, owner: str = "user") -> dict[str, bool]:
    with closing(db()) as connection:
        favorite = connection.execute("SELECT id FROM favorites WHERE source_message_id=?", (message_id,)).fetchone()
        if favorite:
            connection.execute("DELETE FROM favorite_owners WHERE favorite_id=? AND owner=?", (favorite["id"], owner))
            remaining = connection.execute("SELECT 1 FROM favorite_owners WHERE favorite_id=?", (favorite["id"],)).fetchone()
            if not remaining: connection.execute("DELETE FROM favorites WHERE id=?", (favorite["id"],))
            connection.commit()
    return {"ok": True}


def motivation_key(persona_id: str | None) -> str:
    return persona_id or "__default__"


FISHING_WATERS = {
    "willow_bay": {"name": "柳湾", "unlock": 0, "fish": [("银尾鲫", 8, 62), ("青纹鲈", 18, 28), ("月斑鳜", 55, 10)]},
    "mist_lake": {"name": "雾湖", "unlock": 220, "fish": [("雾鳞鱼", 22, 55), ("琉璃鳟", 46, 32), ("星灯鲤", 120, 13)]},
    "cloud_coast": {"name": "云海岸", "unlock": 620, "fish": [("风翼鲷", 50, 52), ("潮鸣鲭", 95, 34), ("极光鳐", 260, 14)]},
}


def default_fishing_state() -> dict[str, Any]:
    return {"coins": 120, "bait": 8, "water": "willow_bay", "turn": 0, "catch": {}, "journal": [], "unlocked": ["willow_bay"]}


def default_claw_state() -> dict[str, Any]:
    return {"coins": 100, "turn": 0, "position": 2, "prizes": ["云朵兔", "星星熊", "橘子猫", "月亮狗", "小海豹"], "inventory": {}, "journal": []}


def default_slots_state() -> dict[str, Any]:
    return {"coins": 100, "turn": 0, "reels": ["✦", "◌", "◇"], "journal": []}


def fishing_pick(state: dict[str, Any]) -> tuple[str, int]:
    digest = hashlib.sha256(f"local-fishing:{state['turn']}:{state['water']}".encode()).digest()
    roll = int.from_bytes(digest[:4], "big") % 100
    cursor = 0
    for name, value, weight in FISHING_WATERS[state["water"]]["fish"]:
        cursor += weight
        if roll < cursor:
            return name, value
    name, value, _ = FISHING_WATERS[state["water"]]["fish"][-1]
    return name, value


def game_catalog() -> list[dict[str, Any]]:
    return [
        {"id": "quiet_fishing", "name": "云汀钓记", "icon": "◌", "status": "playable", "description": "为 AI 与用户共同设计的原创确定性钓鱼游戏。"},
        {"id": "claw_machine", "name": "抓娃娃机", "icon": "◇", "status": "playable", "description": "移动爪子、选择目标并收集娃娃。"},
        {"id": "cloud_slots", "name": "云纹老虎机", "icon": "✦", "status": "playable", "description": "只使用游戏内云贝的确定性三轴小游戏。"},
    ]


def load_game(connection: sqlite3.Connection, game_id: str, persona_id: str | None) -> dict[str, Any]:
    row = connection.execute("SELECT state_json FROM game_saves WHERE game_id=? AND persona_key=?", (game_id, motivation_key(persona_id))).fetchone()
    if row:
        return json.loads(row["state_json"])
    if game_id == "quiet_fishing":
        return default_fishing_state()
    if game_id == "claw_machine":
        return default_claw_state()
    if game_id == "cloud_slots":
        return default_slots_state()
    raise HTTPException(404, "游戏尚未开放")


def save_game(connection: sqlite3.Connection, game_id: str, persona_id: str | None, state: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO game_saves(game_id,persona_key,state_json,updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(game_id,persona_key) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at",
        (game_id, motivation_key(persona_id), json.dumps(state, ensure_ascii=False), now_iso()),
    )


@app.get("/api/games")
def games() -> list[dict[str, Any]]:
    return game_catalog()


@app.get("/api/games/{game_id}/state")
def game_state(game_id: str, persona_id: str | None = None) -> dict[str, Any]:
    with closing(db()) as connection:
        state = load_game(connection, game_id, persona_id)
    return {"game_id": game_id, "state": state, "waters": FISHING_WATERS if game_id == "quiet_fishing" else {}}


@app.post("/api/games/{game_id}/action")
def game_action(game_id: str, body: GameActionIn, persona_id: str | None = None) -> dict[str, Any]:
    with closing(db()) as connection:
        state = load_game(connection, game_id, persona_id)
        events: list[str] = []
        if game_id == "claw_machine":
            if body.action == "move_left": state["position"] = max(0, state["position"] - 1); events.append("爪子向左移动")
            elif body.action == "move_right": state["position"] = min(len(state["prizes"]) - 1, state["position"] + 1); events.append("爪子向右移动")
            elif body.action == "grab":
                if state["coins"] < 10: raise HTTPException(409, "云贝不够")
                state["coins"] -= 10; state["turn"] += 1; prize = state["prizes"][state["position"]]
                roll = int.from_bytes(hashlib.sha256(f"claw:{state['turn']}:{state['position']}".encode()).digest()[:2], "big") % 100
                if roll < 58: state["inventory"][prize] = state["inventory"].get(prize, 0) + 1; events.append(f"抓到了{prize}！")
                else: events.append(f"{prize}晃了一下，又掉回去了")
            else: raise HTTPException(422, "未知游戏动作")
        elif game_id == "cloud_slots":
            if body.action != "spin": raise HTTPException(422, "未知游戏动作")
            bet = 5 * body.amount
            if state["coins"] < bet: raise HTTPException(409, "云贝不够")
            state["coins"] -= bet; symbols = ["✦", "◌", "◇", "☾", "❀"]
            for _ in range(body.amount):
                state["turn"] += 1; digest = hashlib.sha256(f"slots:{state['turn']}".encode()).digest(); state["reels"] = [symbols[digest[i] % len(symbols)] for i in range(3)]
                payout = 40 if len(set(state["reels"])) == 1 else 10 if len(set(state["reels"])) == 2 else 0; state["coins"] += payout
                events.append(" · ".join(state["reels"]) + (f"，赢得 {payout} 云贝" if payout else "，没有连线"))
        elif game_id == "quiet_fishing" and body.action == "cast":
            count = min(body.amount, state["bait"])
            if count < 1:
                raise HTTPException(409, "鱼饵用完了")
            for _ in range(count):
                state["bait"] -= 1; state["turn"] += 1
                name, value = fishing_pick(state)
                state["catch"][name] = state["catch"].get(name, 0) + 1
                events.append(f"钓到了{name}，价值 {value} 枚云贝")
        elif game_id == "quiet_fishing" and body.action == "buy_bait":
            cost = body.amount * 5
            if state["coins"] < cost:
                raise HTTPException(409, "云贝不够")
            state["coins"] -= cost; state["bait"] += body.amount; events.append(f"买了 {body.amount} 份鱼饵")
        elif game_id == "quiet_fishing" and body.action == "sell_all":
            values = {name: value for water in FISHING_WATERS.values() for name, value, _ in water["fish"]}
            income = sum(values.get(name, 0) * count for name, count in state["catch"].items())
            state["coins"] += income; state["catch"] = {}; events.append(f"渔获卖出，得到 {income} 枚云贝")
        elif game_id == "quiet_fishing" and body.action == "travel":
            if body.target not in FISHING_WATERS:
                raise HTTPException(422, "未知水域")
            water = FISHING_WATERS[body.target]
            if body.target not in state["unlocked"]:
                if state["coins"] < water["unlock"]:
                    raise HTTPException(409, "还没有足够云贝解锁这里")
                state["coins"] -= water["unlock"]; state["unlocked"].append(body.target)
            state["water"] = body.target; events.append(f"来到了{water['name']}")
        else:
            raise HTTPException(422, "未知游戏动作")
        state["journal"] = (state["journal"] + events)[-30:]
        save_game(connection, game_id, persona_id, state); connection.commit()
    return {"state": state, "events": events}


AI_GAME_ACTIONS = {
    "quiet_fishing": [{"action": "cast", "amount": 1}, {"action": "buy_bait", "amount": 5}, {"action": "sell_all", "amount": 1}, *[{"action": "travel", "target": key, "amount": 1} for key in FISHING_WATERS]],
    "claw_machine": [{"action": "move_left", "amount": 1}, {"action": "move_right", "amount": 1}, {"action": "grab", "amount": 1}],
    "cloud_slots": [{"action": "spin", "amount": 1}],
}


def game_action_cost(game_id: str, action: dict[str, Any], state: dict[str, Any]) -> int:
    if game_id == "claw_machine" and action["action"] == "grab": return 10
    if game_id == "cloud_slots" and action["action"] == "spin": return 5 * int(action.get("amount", 1))
    if game_id == "quiet_fishing" and action["action"] == "buy_bait": return 5 * int(action.get("amount", 1))
    if game_id == "quiet_fishing" and action["action"] == "travel": return 0 if action.get("target") in state.get("unlocked", []) else int(FISHING_WATERS.get(action.get("target"), {}).get("unlock", 0))
    return 0


def parse_ai_game_choice(text: str, game_id: str) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        lowered = text.lower()
        aliases = {
            "quiet_fishing": [("sell_all", ("sell_all", "出售", "卖掉")), ("buy_bait", ("buy_bait", "买鱼饵", "购买鱼饵")), ("cast", ("cast", "抛竿", "甩一竿", "钓鱼"))],
            "claw_machine": [("move_left", ("move_left", "向左", "左移")), ("move_right", ("move_right", "向右", "右移")), ("grab", ("grab", "抓取", "下爪", "抓娃娃"))],
            "cloud_slots": [("spin", ("spin", "转动", "拉杆", "老虎机"))],
        }
        action = next((name for name, words in aliases.get(game_id, []) if any(word in lowered for word in words)), "")
        if not action:
            raise HTTPException(502, "模型没有返回可执行的游戏动作")
        payload = {"action": action, "comment": text.strip()[:160]}
    candidate = {"action": str(payload.get("action", "")), "amount": int(payload.get("amount", 1) or 1), "target": str(payload.get("target", ""))}
    for allowed in AI_GAME_ACTIONS.get(game_id, []):
        if candidate["action"] == allowed["action"] and ("target" not in allowed or candidate["target"] == allowed["target"]):
            candidate["amount"] = allowed.get("amount", 1)
            return candidate, str(payload.get("comment", "")).strip()[:160]
    raise HTTPException(422, "模型选择了白名单之外的动作")


def fallback_ai_game_choice(game_id: str, state: dict[str, Any], remaining: int) -> tuple[dict[str, Any], str]:
    if game_id == "quiet_fishing":
        if state.get("bait", 0) > 0: return {"action": "cast", "amount": 1}, ""
        if state.get("catch"): return {"action": "sell_all", "amount": 1}, ""
        if remaining >= 25 and state.get("coins", 0) >= 25: return {"action": "buy_bait", "amount": 5}, ""
    if game_id == "claw_machine":
        if remaining >= 10 and state.get("coins", 0) >= 10: return {"action": "grab", "amount": 1}, ""
        return {"action": "move_right", "amount": 1}, ""
    if game_id == "cloud_slots" and remaining >= 5 and state.get("coins", 0) >= 5:
        return {"action": "spin", "amount": 1}, ""
    raise HTTPException(409, "当前局面没有可安全执行的游戏动作")


@app.post("/api/games/{game_id}/ai-turn")
async def ai_game_turn(game_id: str, body: AiGameTurnIn) -> dict[str, Any]:
    if game_id not in AI_GAME_ACTIONS: raise HTTPException(404, "游戏尚未开放 AI 游玩")
    with closing(db()) as connection:
        provider = connection.execute("SELECT * FROM providers WHERE id=? AND enabled=1", (body.provider_id,)).fetchone()
        persona = connection.execute("SELECT name, prompt FROM personas WHERE id=?", (body.persona_id,)).fetchone() if body.persona_id else None
        persona_name = persona["name"] if persona else "当前人格"
    if not provider: raise HTTPException(404, "API 线路不存在")
    decisions, remaining = [], body.max_spend
    async with httpx.AsyncClient(timeout=35) as client:
        for _ in range(body.turns):
            with closing(db()) as connection: current = load_game(connection, game_id, body.persona_id)
            instruction = f"""你正在 Atherloom 中玩游戏 {game_id}。\n当前状态：{json.dumps(current, ensure_ascii=False)}\n允许动作：{json.dumps(AI_GAME_ACTIONS[game_id], ensure_ascii=False)}\n剩余可花云贝预算：{remaining}。\n只返回一个 JSON 对象：{{\"action\":\"白名单动作\",\"amount\":1,\"target\":\"需要时填写\",\"comment\":\"一句当轮想法\"}}。不要输出 Markdown。"""
            if persona: instruction = persona["prompt"] + "\n\n" + instruction
            headers = provider_headers(provider["protocol"], provider["api_key"], provider["custom_headers"])
            if provider["protocol"] == "anthropic":
                payload = {"model": provider["model"], "max_tokens": 96, "temperature": 0.2, "messages": [{"role": "user", "content": instruction}]}
            else:
                payload = {"model": provider["model"], "max_tokens": 96, "temperature": 0.2, "stream": False, "messages": [{"role": "user", "content": instruction}]}
            response = await client.post(provider_endpoint(provider["base_url"], provider["protocol"]), headers=headers, json=payload)
            if response.status_code >= 400: raise HTTPException(502, f"游戏 AI 请求失败：{response.status_code}")
            data = response.json(); text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text") if provider["protocol"] == "anthropic" else data.get("choices", [{}])[0].get("message", {}).get("content", "")
            try:
                choice, comment = parse_ai_game_choice(text, game_id)
            except HTTPException as error:
                if error.status_code not in (422, 502): raise
                choice, comment = fallback_ai_game_choice(game_id, current, remaining)
            cost = game_action_cost(game_id, choice, current)
            if cost > remaining: break
            result = game_action(game_id, GameActionIn(**choice), body.persona_id); remaining -= cost
            if comment:
                with closing(db()) as connection:
                    state = load_game(connection, game_id, body.persona_id); state["journal"] = (state.get("journal", []) + [f"{persona_name} · 心里话：{comment}"])[-30:]; save_game(connection, game_id, body.persona_id, state); connection.commit(); result["state"] = state
            decisions.append({"choice": choice, "comment": comment, "events": result["events"]})
    with closing(db()) as connection: final_state = load_game(connection, game_id, body.persona_id)
    return {"state": final_state, "decisions": decisions, "spent": body.max_spend - remaining}


def load_motivation(connection: sqlite3.Connection, persona_id: str | None) -> tuple[bool, dict[str, Any], str]:
    row = connection.execute("SELECT * FROM motivation_states WHERE persona_key=?", (motivation_key(persona_id),)).fetchone()
    if not row:
        return False, default_state(), "limited"
    mode = row["offline_mode"] if "offline_mode" in row.keys() else "limited"
    return bool(row["enabled"]), normalize(json.loads(row["state_json"])), mode


def save_motivation(connection: sqlite3.Connection, persona_id: str | None, enabled: bool, state: dict[str, Any], offline_mode: str = "limited") -> None:
    connection.execute(
        "INSERT INTO motivation_states(persona_key,enabled,state_json,offline_mode,updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(persona_key) DO UPDATE SET enabled=excluded.enabled,state_json=excluded.state_json,"
        "offline_mode=excluded.offline_mode,updated_at=excluded.updated_at",
        (motivation_key(persona_id), int(enabled), json.dumps(normalize(state), ensure_ascii=False), offline_mode, now_iso()),
    )


def catch_up_motivation(state: dict[str, Any], offline_mode: str) -> tuple[dict[str, Any], int]:
    state = normalize(state)
    if offline_mode == "frozen":
        return state, 0
    try:
        last_tick = datetime.fromisoformat(state["last_tick"].replace("Z", "+00:00"))
        elapsed = max(0, (datetime.now(timezone.utc) - last_tick).total_seconds())
    except (TypeError, ValueError):
        elapsed = 0
    cap = 3 if offline_mode == "limited" else 96
    steps = min(cap, int(elapsed // 1800))
    for _ in range(steps):
        state = tick(state)["state"]
    return state, steps


@app.get("/api/motivation/{persona_key}")
def get_motivation(persona_key: str) -> dict[str, Any]:
    persona_id = None if persona_key == "__default__" else persona_key
    with closing(db()) as connection:
        enabled, state, offline_mode = load_motivation(connection, persona_id)
        state, catch_up_ticks = catch_up_motivation(state, offline_mode) if enabled else (state, 0)
        if catch_up_ticks:
            save_motivation(connection, persona_id, enabled, state, offline_mode)
            connection.commit()
    return {"enabled": enabled, "state": state, "offline_mode": offline_mode, "catch_up_ticks": catch_up_ticks, "drives": DRIVES, "events": list(EVENTS)}


@app.put("/api/motivation/{persona_key}/enabled")
def set_motivation_enabled(persona_key: str, body: MotivationEnabledIn) -> dict[str, Any]:
    persona_id = None if persona_key == "__default__" else persona_key
    with closing(db()) as connection:
        _, state, _ = load_motivation(connection, persona_id)
        save_motivation(connection, persona_id, body.enabled, state, body.offline_mode)
        connection.commit()
    return {"enabled": body.enabled, "state": state, "offline_mode": body.offline_mode}


@app.post("/api/motivation/{persona_key}/event")
def motivation_event(persona_key: str, body: MotivationEventIn) -> dict[str, Any]:
    if body.event not in EVENTS:
        raise HTTPException(422, "未知的动机事件")
    persona_id = None if persona_key == "__default__" else persona_key
    with closing(db()) as connection:
        enabled, state, offline_mode = load_motivation(connection, persona_id)
        changes = apply_event(state, body.event)
        save_motivation(connection, persona_id, enabled, state, offline_mode)
        connection.commit()
    return {"enabled": enabled, "state": state, "changes": changes}


@app.post("/api/motivation/{persona_key}/tick")
def motivation_tick(persona_key: str) -> dict[str, Any]:
    persona_id = None if persona_key == "__default__" else persona_key
    with closing(db()) as connection:
        enabled, state, offline_mode = load_motivation(connection, persona_id)
        result = tick(state)
        save_motivation(connection, persona_id, enabled, result["state"], offline_mode)
        connection.commit()
    return {"enabled": enabled, **result}


@app.post("/api/motivation/{persona_key}/reset")
def motivation_reset(persona_key: str) -> dict[str, Any]:
    persona_id = None if persona_key == "__default__" else persona_key
    state = default_state()
    with closing(db()) as connection:
        enabled, _, offline_mode = load_motivation(connection, persona_id)
        save_motivation(connection, persona_id, enabled, state, offline_mode)
        connection.commit()
    return {"enabled": enabled, "state": state, "drives": DRIVES, "events": list(EVENTS)}


def active_worldbook_entries(connection: sqlite3.Connection, ids: list[str], messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not ids: return []
    placeholders=",".join("?" for _ in ids);rows=connection.execute(f"SELECT * FROM worldbooks WHERE enabled=1 AND id IN ({placeholders})",ids).fetchall();active=[]
    for row in rows:
        book=worldbook_dict(row)
        for entry in book["entries"]:
            if not entry.get("enabled",True): continue
            depth=max(1,int(entry.get("scan_depth") or 4));source="\n".join(str(item.get("content", "")) for item in messages[-depth:]);keywords=entry.get("keywords") or []
            matched=bool(entry.get("constant")) or not keywords
            for keyword in keywords:
                try:
                    if entry.get("use_regex") and re.search(keyword,source,0 if entry.get("case_sensitive") else re.IGNORECASE): matched=True
                    elif (keyword in source if entry.get("case_sensitive") else keyword.lower() in source.lower()): matched=True
                except re.error: continue
            if matched: active.append(entry)
    return sorted(active,key=lambda item:int(item.get("priority") or 0))


def relevant_roleplay_archive(connection: sqlite3.Connection, query: str, limit: int = 2) -> str:
    query_terms = text_bigrams(query)
    if not query_terms:
        return ""
    ranked: list[tuple[int, sqlite3.Row, dict[str, Any], list[dict[str, Any]]]] = []
    for row in connection.execute("SELECT * FROM roleplay_stories ORDER BY updated_at DESC"):
        cast = json.loads(row["cast_json"] or "[]")
        state = json.loads(row["state_json"] or "{}")
        searchable = " ".join([
            row["title"], row["player_name"], row["premise"],
            " ".join(str(actor.get("name", "")) for actor in cast),
            str(state.get("rolling_summary", "")),
        ])
        overlap = len(query_terms & text_bigrams(searchable))
        explicit = any(name and name in query for name in [row["title"], row["player_name"], *[str(actor.get("name", "")) for actor in cast]])
        if overlap >= 2 or explicit:
            ranked.append((overlap + (10 if explicit else 0), row, state, cast))
    if not ranked:
        return ""
    ranked.sort(key=lambda item: item[0], reverse=True)
    archives = []
    for _, row, state, cast in ranked[:limit]:
        archives.append(
            f"故事《{row['title']}》；玩家在故事中的名字：{row['player_name']}；"
            f"登场角色：{', '.join(str(actor.get('name', '')) for actor in cast)}；"
            f"状态：{'已收场' if row['status'] == 'completed' else '进行中'}；"
            f"精确停在第 {state.get('turn_number', 0)} 回合。\n"
            f"剧情档案：{state.get('rolling_summary', '')}\n"
            f"最后场景：{state.get('scene', '')}"
        )
    return (
        "<fictional_roleplay_archive>\n" + "\n\n".join(archives) +
        "\n</fictional_roleplay_archive>\n"
        "以上内容是明确标记的虚构角色剧场档案。可以据此回忆剧情和停场位置，"
        "但不得当作用户的现实经历或现实长期记忆。"
    )


def load_chat_context(connection: sqlite3.Connection, body: ChatIn, cutoff: str | None = None) -> tuple[sqlite3.Row, str, list[dict[str, str]]]:
    provider = connection.execute("SELECT * FROM providers WHERE id=? AND enabled=1", (body.provider_id,)).fetchone()
    if not provider:
        raise HTTPException(404, "API 配置不存在或已停用")
    persona_prompt = ""
    persona_config = normalize_persona_config({})
    if body.persona_id:
        persona = connection.execute("SELECT * FROM personas WHERE id=?", (body.persona_id,)).fetchone()
        config_row = connection.execute("SELECT config_json FROM persona_configs WHERE persona_id=?", (body.persona_id,)).fetchone()
        persona_config = normalize_persona_config(config_row["config_json"] if config_row else {})
        persona_prompt = f"<assistant_persona active=\"true\">\n{persona['prompt']}\n</assistant_persona>" if persona and persona["prompt"].strip() else ""
    conversation = connection.execute("SELECT * FROM conversations WHERE id=?", (body.conversation_id,)).fetchone()
    if not conversation:
        raise HTTPException(404, "会话不存在")
    query = """SELECT messages.role, messages.content FROM messages
      WHERE messages.conversation_id=?
      AND NOT EXISTS (SELECT 1 FROM message_trash t WHERE t.message_id=messages.id)
      AND (messages.role!='assistant' OR messages.parent_message_id IS NULL OR messages.id=COALESCE(
        (SELECT s.assistant_message_id FROM message_selections s WHERE s.conversation_id=messages.conversation_id AND s.parent_message_id=messages.parent_message_id),
        (SELECT m2.id FROM messages m2 WHERE m2.conversation_id=messages.conversation_id AND m2.parent_message_id=messages.parent_message_id AND NOT EXISTS (SELECT 1 FROM message_trash t2 WHERE t2.message_id=m2.id) ORDER BY m2.created_at DESC LIMIT 1)
      ))"""
    params: list[Any] = [body.conversation_id]
    if cutoff:
        query += " AND created_at<=?"
        params.append(cutoff)
    query += " ORDER BY created_at"
    messages = [{"role": row["role"], "content": row["content"]} for row in connection.execute(query, params)]
    if not persona_config["history_enabled"]:
        messages = []
    time_context = f"当前本地时间（由用户设备提供）：{body.local_time}" if body.local_time else ""
    proactive_row = connection.execute("SELECT value FROM app_settings WHERE key='proactive_questions'").fetchone()
    proactive_questions = proactive_row and proactive_row["value"] == "true"
    question_context = ("用户允许你在合适时主动提问、自然追问或发起新话题。需要用户选择时，先自然地说一句引导语，再在回复末尾严格输出 <questions>[{\"question\":\"问题\",\"options\":[\"选项一\",\"选项二\",\"选项三\"]}]</questions>；可包含 1 至 4 个问题，每题 2 至 5 个简短选项，不要在标签外重复选项。用户明确要求你提问时必须使用此格式。不要机械地每轮都提问。" if proactive_questions else "除非完成当前请求确实缺少必要信息，否则不要主动反问或发起问卷；优先直接回应用户。")
    formatting_context = "界面支持 Markdown。你可以根据语义有节制地使用 **粗体**、*斜体*、标题、引用、列表与代码块；不要为了装饰而过度格式化。"
    tool_names = [name for name, enabled in persona_config["tools"].items() if enabled]
    tool_context = f"该人格启用的本地能力偏好：{', '.join(tool_names)}。只有宿主实际提供的能力才可调用。" if tool_names else ""
    game_tool_context = "宿主提供云汀钓记、抓娃娃机和云纹老虎机游戏工具。用户要求你去玩时，宿主会在回复前执行工具并提供 <verified_game_result>。只有收到该结果才能声称自己玩过，并应自然讲述真实动作、收获与心里话；没有结果时不得虚构游戏经历。"
    game_context = f"<verified_game_result>\n{body.game_context}\n</verified_game_result>\n这是宿主刚刚真实执行的游戏结果。请以当前人格自然回应，可以主动谈起收获与心情，不要声称没有玩过。" if body.game_context else ""
    if body.media_context and body.media_context.lstrip().startswith("书籍："):
        media_context = f"<shared_reading_evidence>\n{body.media_context}\n</shared_reading_evidence>\n只能依据用户主动提供的本地阅读片段讨论本书；不要假装读过未提供的正文，也不要推断后续内容。"
    else:
        media_context = f"<shared_watch_evidence>\n{body.media_context}\n</shared_watch_evidence>\n只能依据以上播放点之前的证据讨论当前影片；不要剧透、不要假装看见字幕未提供的画面。" if body.media_context else ""
    scan_messages=[*messages,{"role":"user","content":body.content}]
    entries=active_worldbook_entries(connection,body.worldbook_ids,scan_messages);before=[entry["content"] for entry in entries if entry.get("position")=="system_before"];after=[entry["content"] for entry in entries if entry.get("position")=="system_after" or entry.get("role")=="system" and str(entry.get("position","")).startswith("history_")]
    for entry in reversed([item for item in entries if item.get("position")=="history_before" and item.get("role")!="system"]): messages.insert(0,{"role":entry.get("role","user"),"content":entry["content"]})
    for entry in [item for item in entries if item.get("position")=="history_after" and item.get("role")!="system"]: messages.append({"role":entry.get("role","user"),"content":entry["content"]})
    worldbook_before="<worldbook_instructions>\n"+"\n\n".join(before)+"\n</worldbook_instructions>" if before else "";worldbook_after="<worldbook_instructions>\n"+"\n\n".join(after)+"\n</worldbook_instructions>" if after else ""
    roleplay_context = relevant_roleplay_archive(connection, body.content)
    system_parts = [part for part in (worldbook_before,persona_prompt,worldbook_after,conversation["summary"] if persona_config["history_enabled"] else "", time_context, question_context, formatting_context, tool_context, game_tool_context, game_context, media_context, roleplay_context) if part]
    if system_parts:
        messages.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
    return provider, persona_prompt, messages


def provider_endpoint(base_url: str, protocol: str) -> str:
    base = base_url.rstrip("/")
    if protocol == "anthropic":
        return base + ("/messages" if base.endswith("/v1") else "/v1/messages")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def attachment_content(text: str, attachments: list[dict[str, Any]], protocol: str, vision_mode: str = "auto") -> str | list[dict[str, Any]]:
    if not attachments:
        return text
    if vision_mode == "text" and any(item.get("kind") == "image" for item in attachments):
        raise HTTPException(422, "当前线路设置为仅文本，不能发送图片。请删除图片或切换到支持看图的线路。")
    anthropic_images = vision_mode == "anthropic" or (vision_mode == "auto" and protocol == "anthropic")
    if anthropic_images:
        blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for item in attachments:
            data = str(item.get("data", "")); encoded = data.split(",", 1)[1] if "," in data else ""
            if item.get("kind") == "image" and encoded:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": item.get("mime", "image/jpeg"), "data": encoded}})
            elif item.get("kind") == "pdf" and encoded:
                blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": encoded}})
            elif item.get("text"):
                blocks.append({"type": "text", "text": f"文件：{item.get('name', '未命名')}\n{str(item['text'])[:120000]}"})
        return blocks
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for item in attachments:
        if item.get("kind") == "image" and item.get("data"):
            parts.append({"type": "image_url", "image_url": {"url": item["data"]}})
        elif item.get("text"):
            parts.append({"type": "text", "text": f"文件：{item.get('name', '未命名')}\n{str(item['text'])[:120000]}"})
        else:
            parts.append({"type": "text", "text": f"[当前兼容线路无法直接读取文件 {item.get('name', '未命名')}]"})
    return parts


def append_pending_user(messages: list[dict[str, Any]], body: ChatIn) -> None:
    if not body.reuse_user_message_id:
        messages.append({"role": "user", "content": body.content})


def provider_models_endpoint(base_url: str, protocol: str) -> str:
    base = base_url.rstrip("/")
    if protocol == "anthropic":
        return base + ("/models" if base.endswith("/v1") else "/v1/models")
    return base + "/models"


def provider_headers(protocol: str, api_key: str, custom_headers_raw: str = "{}") -> dict[str, str]:
    if protocol == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    else:
        headers = {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}
    try:
        custom_headers = json.loads(custom_headers_raw or "{}")
        if not isinstance(custom_headers, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise HTTPException(422, "自定义请求头不是有效 JSON") from exc
    headers.update({str(key): str(value) for key, value in custom_headers.items()})
    return headers


def extract_model_ids(payload: Any) -> list[str]:
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    models: list[str] = []
    for row in rows:
        model = row.get("id") if isinstance(row, dict) else row if isinstance(row, str) else None
        if model and str(model) not in models:
            models.append(str(model))
    return sorted(models, key=str.lower)


@app.post("/api/providers/models")
async def list_provider_models(body: ProviderProbe) -> dict[str, Any]:
    api_key = body.api_key
    if not api_key and body.provider_id:
        with closing(db()) as connection:
            saved = connection.execute("SELECT api_key FROM providers WHERE id=?", (body.provider_id,)).fetchone()
        if not saved:
            raise HTTPException(404, "API 线路不存在")
        api_key = saved["api_key"]
    headers = provider_headers(body.protocol, api_key, body.custom_headers)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(provider_models_endpoint(body.base_url, body.protocol), headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(502, f"无法连接：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(response.status_code, f"拉取失败：HTTP {response.status_code} · {response.text[:240]}")
    try:
        models = extract_model_ids(response.json())
    except json.JSONDecodeError as exc:
        raise HTTPException(502, "模型列表不是有效 JSON") from exc
    return {"models": models, "count": len(models)}


@app.post("/api/providers/test")
async def test_provider(body: ProviderIn) -> dict[str, Any]:
    protocol = body.protocol
    signature = f"{body.base_url} {body.model}".lower()
    if protocol == "openai" and "deepseek" in signature:
        protocol = "deepseek"
    elif protocol == "openai" and ("bigmodel.cn" in signature or body.model.lower().startswith("glm-")):
        protocol = "glm"
    api_key = body.api_key
    if not api_key and body.source_provider_id:
        with closing(db()) as connection:
            saved = connection.execute("SELECT api_key FROM providers WHERE id=?", (body.source_provider_id,)).fetchone()
        if not saved:
            raise HTTPException(404, "API 线路不存在")
        api_key = saved["api_key"]
    if not api_key:
        raise HTTPException(422, "API Key is required unless the saved route already has one")
    headers = provider_headers(protocol, api_key, body.custom_headers)
    url = provider_models_endpoint(body.base_url, protocol)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(502, f"无法连接：{exc}") from exc
    if response.status_code == 404:
        endpoint = provider_endpoint(body.base_url, protocol)
        probe = {"model": body.model, "max_tokens": 1, "messages": [{"role": "user", "content": "Hi"}]}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(endpoint, headers=headers, json=probe)
        except httpx.RequestError as exc:
            raise HTTPException(502, f"Connection failed: {exc}") from exc
        if response.status_code >= 400:
            raise HTTPException(response.status_code, f"Validation failed: HTTP {response.status_code} - {response.text[:240]}")
        return {"ok": True, "reachable": True, "models_supported": False, "message": "网关可以访问，但未提供模型列表接口"}
    if response.status_code >= 400:
        raise HTTPException(response.status_code, f"验证失败：HTTP {response.status_code} · {response.text[:240]}")
    try:
        payload = response.json()
        count = len(payload.get("data", [])) if isinstance(payload, dict) else 0
    except json.JSONDecodeError:
        count = 0
    return {"ok": True, "reachable": True, "models_supported": True, "model_count": count, "message": f"连接成功，读取到 {count} 个模型"}


def local_title(content: str) -> str:
    cleaned = " ".join(content.split()).strip(" ，。！？,.!?：:")
    return (cleaned[:22] + ("…" if len(cleaned) > 22 else "")) or "新对话"


def text_bigrams(value: str) -> set[str]:
    compact = "".join(value.lower().split())
    return {compact[index:index + 2] for index in range(max(0, len(compact) - 1))}


def memory_type_hints(query: str) -> set[str]:
    hints = set()
    groups = {
        "emotion": ("难过", "开心", "害怕", "焦虑", "生气", "感受", "情绪", "为什么不"),
        "event": ("那次", "发生", "后来", "以前", "第一次", "什么时候"),
        "preference": ("喜欢", "讨厌", "偏好", "习惯", "想吃", "想看"),
        "promise": ("答应", "约定", "承诺", "说好", "别忘"),
        "relationship": ("关系", "是谁", "我们", "朋友", "伴侣"),
        "fact": ("什么", "多少", "哪里", "是谁", "事实", "资料"),
    }
    for kind, words in groups.items():
        if any(word in query for word in words):
            hints.add(kind)
    return hints


def retrieve_memories(connection: sqlite3.Connection, query: str, limit: int = 6, char_budget: int = 6000) -> list[dict[str, Any]]:
    query_terms = text_bigrams(query)
    if not query_terms:
        return []
    rows = list(connection.execute("SELECT * FROM memories WHERE archived=0 AND deleted_at IS NULL"))
    if not rows:
        return []
    documents = [Counter(text_bigrams(f"{row['title']} {row['content']}")) for row in rows]
    frequencies = Counter(term for terms in documents for term in terms)
    average_length = sum(sum(terms.values()) for terms in documents) / len(documents)
    type_hints = memory_type_hints(query)
    now = datetime.now(timezone.utc)
    ranked = []
    for row, terms in zip(rows, documents):
        length = max(1, sum(terms.values()))
        score = 0.0
        matched = []
        for term in query_terms & terms.keys():
            document_frequency = frequencies[term]
            idf = math.log(1 + (len(rows) - document_frequency + .5) / (document_frequency + .5))
            frequency = terms[term]
            score += idf * (frequency * 2.2) / (frequency + 1.2 * (.25 + .75 * length / max(1, average_length)))
            if idf > .35:
                matched.append(term)
        if not score:
            continue
        if row["kind"] in type_hints:
            score += .35
        if row["starred"]:
            score += .2
        try:
            age_days = max(0, (now - datetime.fromisoformat(row["updated_at"])).days)
            score += .12 / (1 + age_days / 90)
        except (TypeError, ValueError):
            pass
        ranked.append({"score": score, "row": row, "terms": set(terms), "matched": matched})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    ranked_ids = {item["row"]["id"] for item in ranked}
    for row, terms in zip(rows, documents):
        if row["starred"] and row["id"] not in ranked_ids:
            ranked.append({"score": .32, "row": row, "terms": set(terms), "matched": []})
    if not ranked:
        recent = sorted(zip(rows, documents), key=lambda item: item[0]["updated_at"], reverse=True)[:min(3, limit)]
        ranked = [{"score": .08, "row": row, "terms": set(terms), "matched": []} for row, terms in recent]

    selected = []
    used_chars = 0
    while ranked and len(selected) < limit:
        best = max(ranked, key=lambda item: item["score"] - .45 * max(
            (len(item["terms"] & chosen["terms"]) / max(1, len(item["terms"] | chosen["terms"])) for chosen in selected),
            default=0,
        ))
        ranked.remove(best)
        content = best["row"]["content"]
        if selected and used_chars + len(content) > char_budget:
            continue
        selected.append(best)
        used_chars += len(content)
    return [{
        "id": item["row"]["id"], "title": item["row"]["title"], "kind": item["row"]["kind"],
        "content": item["row"]["content"], "score": round(item["score"], 4),
        "reason": "类型与主题匹配" if item["row"]["kind"] in type_hints else "主题相关",
    } for item in selected]


async def model_title(client: httpx.AsyncClient, provider: sqlite3.Row, content: str, headers: dict[str, str]) -> str:
    instruction = f"请把下面的用户消息概括成一个不超过12个汉字的中文对话标题。只输出标题，不要引号和解释：\n{content[:1000]}"
    if provider["protocol"] == "anthropic":
        payload = {"model": provider["model"], "max_tokens": 40, "messages": [{"role": "user", "content": instruction}]}
    else:
        payload = {"model": provider["model"], "max_tokens": 40, "messages": [{"role": "user", "content": instruction}]}
    response = await client.post(provider_endpoint(provider["base_url"], provider["protocol"]), headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    if provider["protocol"] == "anthropic":
        title = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
    else:
        title = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return title.strip().strip('"“”')[:30] or local_title(content)


@app.post("/api/chat")
async def chat(body: ChatIn) -> StreamingResponse:
    user_id = body.reuse_user_message_id or str(uuid.uuid4())
    created = now_iso()
    with closing(db()) as connection:
        cutoff = None
        if body.reuse_user_message_id:
            reused = connection.execute("SELECT * FROM messages WHERE id=? AND conversation_id=? AND role='user'", (body.reuse_user_message_id, body.conversation_id)).fetchone()
            if not reused:
                raise HTTPException(404, "找不到要重新生成的用户消息")
            cutoff = reused["created_at"]
            body.content = reused["content"]
        provider, _, messages = load_chat_context(connection, body, cutoff)
        config_row = connection.execute("SELECT config_json FROM persona_configs WHERE persona_id=?", (body.persona_id,)).fetchone() if body.persona_id else None
        persona_config = normalize_persona_config(config_row["config_json"] if config_row else {})
        bound_names = [str(name) for name in persona_config["mcp_servers"] if str(name).strip()]
        bound_mcp_servers: list[dict[str, Any]] = []
        if bound_names:
            placeholders = ",".join("?" for _ in bound_names)
            bound_mcp_servers = [dict(row) for row in connection.execute(f"SELECT * FROM mcp_servers WHERE enabled=1 AND name IN ({placeholders})", bound_names)]
        permission_row = connection.execute("SELECT value FROM app_settings WHERE key='tool_permissions'").fetchone()
        permissions = json.loads(permission_row["value"]) if permission_row else {"memory_read": "allow"}
        memory_sources = retrieve_memories(connection, body.content) if permissions.get("memory_read") == "allow" and persona_config["memory_enabled"] else []
        if memory_sources:
            memory_context = "<relevant_memories>\n" + "\n\n".join(
                f"[memory:{item['id']}] {item['title']}\n{item['content']}" for item in memory_sources
            ) + "\n</relevant_memories>"
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] += "\n\n" + memory_context
            else:
                messages.insert(0, {"role": "system", "content": memory_context})
        inner_key = motivation_key(body.persona_id)
        journal_rows = connection.execute(
            "SELECT title,content,space,author FROM journal_entries WHERE persona_key=? AND visible_to_ai=1 ORDER BY updated_at DESC LIMIT 12",
            (inner_key,),
        ).fetchall()
        board_rows = connection.execute(
            "SELECT content,author FROM board_messages WHERE persona_key=? AND visible_to_ai=1 ORDER BY created_at DESC LIMIT 20",
            (inner_key,),
        ).fetchall()
        if journal_rows or board_rows:
            private_context = "<shared_journal_and_board>\n"
            private_context += "\n".join(f"[diary:{row['space']}:{row['author']}] {row['title']}\n{row['content']}" for row in journal_rows)
            private_context += "\n" + "\n".join(f"[board:{row['author']}] {row['content']}" for row in board_rows)
            private_context += "\nOnly use entries explicitly marked visible_to_ai. Never reveal or infer sealed entries.\n</shared_journal_and_board>"
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] += "\n\n" + private_context
            else:
                messages.insert(0, {"role": "system", "content": private_context})
        motivation_enabled, motivation_state, motivation_offline_mode = load_motivation(connection, body.persona_id)
        if motivation_enabled:
            motivation_state, _ = catch_up_motivation(motivation_state, motivation_offline_mode)
            apply_event(motivation_state, "contact_message")
            motivation_result = tick(motivation_state)
            motivation_context = context_summary(motivation_result["state"])
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] += "\n\n" + motivation_context
            else:
                messages.insert(0, {"role": "system", "content": motivation_context})
            save_motivation(connection, body.persona_id, True, motivation_result["state"], motivation_offline_mode)
        if not body.reuse_user_message_id:
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, 'user', ?, ?, ?, ?, '', NULL)",
                (user_id, body.conversation_id, body.content, body.provider_id, provider["model"], created),
            )
        connection.execute("UPDATE conversations SET provider_id=?, persona_id=?, updated_at=? WHERE id=?", (body.provider_id, body.persona_id, created, body.conversation_id))
        connection.commit()
    append_pending_user(messages, body)

    async def stream():
        full = ""
        reasoning = ""
        try:
            if memory_sources:
                yield json.dumps({"memory_sources": [{"id": item["id"], "title": item["title"], "kind": item["kind"]} for item in memory_sources]}, ensure_ascii=False) + "\n"
            async with httpx.AsyncClient(timeout=180) as client:
                provider_messages = [dict(message) for message in messages]
                if body.attachments:
                    for message in reversed(provider_messages):
                        if message["role"] == "user":
                            message["content"] = attachment_content(body.content, body.attachments, provider["protocol"], provider["vision_mode"])
                            break
                if provider["protocol"] == "anthropic":
                    system = "\n\n".join(m["content"] for m in provider_messages if m["role"] == "system")
                    payload = {"model": provider["model"], "max_tokens": provider["max_tokens"], "temperature": provider["temperature"], "top_p": provider["top_p"], "stream": bool(provider["stream_enabled"]), "messages": [m for m in provider_messages if m["role"] != "system"]}
                    if system:
                        explicit_anthropic_cache = provider["cache_mode"] in ("auto", "anthropic") and provider["prompt_cache"]
                        payload["system"] = [{"type": "text", "text": system, **({"cache_control": {"type": "ephemeral"}} if explicit_anthropic_cache else {})}]
                    headers = {"x-api-key": provider["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"}
                    url = provider_endpoint(provider["base_url"], "anthropic")
                else:
                    payload = {"model": provider["model"], "max_tokens": provider["max_tokens"], "temperature": provider["temperature"], "top_p": provider["top_p"], "stream": bool(provider["stream_enabled"]), "messages": provider_messages}
                    if provider["cache_mode"] == "openai" and provider["prompt_cache_key"]:
                        payload["prompt_cache_key"] = provider["prompt_cache_key"]
                    if provider["protocol"] in ("deepseek", "glm") and provider["thinking_enabled"]:
                        payload["thinking"] = {"type": "enabled"}
                    headers = {"Authorization": f"Bearer {provider['api_key']}", "content-type": "application/json"}
                    url = provider_endpoint(provider["base_url"], provider["protocol"])
                try:
                    custom_headers = json.loads(provider["custom_headers"] or "{}")
                    if not isinstance(custom_headers, dict):
                        raise ValueError
                    headers.update({str(key): str(value) for key, value in custom_headers.items()})
                except (json.JSONDecodeError, ValueError, TypeError):
                    yield json.dumps({"error": "这条 API 线路的自定义请求头不是有效 JSON"}, ensure_ascii=False) + "\n"
                    return
                direct_answer = False
                builtin_tools, builtin_bindings = builtin_tool_catalog(permissions)
                if bound_mcp_servers or builtin_tools:
                    mcp_tools, mcp_bindings = await bound_mcp_catalog(bound_mcp_servers)
                    mcp_tools = [*builtin_tools, *mcp_tools]
                    mcp_bindings = {**builtin_bindings, **mcp_bindings}
                    if mcp_tools:
                        probe_payload = dict(payload)
                        probe_payload["stream"] = False
                        if provider["protocol"] == "anthropic":
                            probe_payload["tools"] = [{"name": tool["name"], "description": tool["description"], "input_schema": tool["input_schema"]} for tool in mcp_tools]
                        else:
                            probe_payload["tools"] = [{"type": "function", "function": {"name": tool["name"], "description": tool["description"], "parameters": tool["input_schema"]}} for tool in mcp_tools]
                        probe = await client.post(url, headers=headers, json=probe_payload)
                        if probe.status_code >= 400:
                            yield json.dumps({"error": f"API {probe.status_code}: {probe.text[:500]}"}, ensure_ascii=False) + "\n"
                            return
                        data = probe.json()
                        if provider["protocol"] == "anthropic":
                            blocks = data.get("content", [])
                            calls = [block for block in blocks if block.get("type") == "tool_use" and block.get("name") in mcp_bindings]
                            text_content = "".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text")
                            dsml_calls = [
                                call for call in parse_dsml_tool_calls(text_content)
                                if call.get("function", {}).get("name") in mcp_bindings
                            ] if not calls else []
                            if calls:
                                results = []
                                for call in calls[:8]:
                                    server, original = mcp_bindings[call["name"]]
                                    try:
                                        policy = expanded_mcp_server(server).get("tool_policies", {}).get(original, "allow")
                                        if policy == "ask":
                                            raise PermissionError("该工具设置为“每次询问”，当前未获得用户确认")
                                        tool_arguments = dict(call.get("input") or {})
                                        if server.get("transport") == "builtin":
                                            tool_arguments["_persona_key"] = motivation_key(body.persona_id)
                                            tool_arguments["_conversation_id"] = body.conversation_id
                                            tool_arguments["_source_message_id"] = user_id
                                        result = await invoke_server_tool(server, original, tool_arguments)
                                        content, is_error = mcp_result_text(result), False
                                        record_mcp_audit(server["id"], original, "success", conversation_id=body.conversation_id, user_message_id=user_id)
                                    except Exception as exc:
                                        content, is_error = f"MCP 工具调用失败：{exc}", True
                                        record_mcp_audit(server["id"], original, "blocked" if isinstance(exc, PermissionError) else "error", str(exc), body.conversation_id, user_id)
                                    results.append({"type": "tool_result", "tool_use_id": call["id"], "content": content[:50000], "is_error": is_error})
                                probe_payload.pop("tools", None)
                                probe_payload["messages"] = [*probe_payload["messages"], {"role": "assistant", "content": blocks}, {"role": "user", "content": results}]
                                payload = probe_payload
                                payload["stream"] = bool(provider["stream_enabled"])
                            elif dsml_calls:
                                dsml_results = []
                                for call in dsml_calls[:8]:
                                    function = call["function"]
                                    server, original = mcp_bindings[function["name"]]
                                    try:
                                        policy = expanded_mcp_server(server).get("tool_policies", {}).get(original, "allow")
                                        if policy == "ask":
                                            raise PermissionError("该工具设置为“每次询问”，当前未获得用户确认")
                                        arguments = json.loads(function.get("arguments") or "{}")
                                        if server.get("transport") == "builtin":
                                            arguments["_persona_key"] = motivation_key(body.persona_id)
                                            arguments["_conversation_id"] = body.conversation_id
                                            arguments["_source_message_id"] = user_id
                                        result = await invoke_server_tool(server, original, arguments)
                                        content = mcp_result_text(result)
                                        record_mcp_audit(server["id"], original, "success", conversation_id=body.conversation_id, user_message_id=user_id)
                                    except Exception as exc:
                                        content = f"MCP 工具调用失败：{exc}"
                                        record_mcp_audit(server["id"], original, "blocked" if isinstance(exc, PermissionError) else "error", str(exc), body.conversation_id, user_id)
                                    dsml_results.append({"tool": function["name"], "result": content[:50000]})
                                result_prompt = (
                                    "<tool_results>\n"
                                    + "\n".join(json.dumps(item, ensure_ascii=False) for item in dsml_results)
                                    + "\n</tool_results>\n以上是宿主刚刚执行工具得到的真实结果。"
                                    "请根据结果直接回答用户，不要再次输出 DSML、工具调用代码或伪造结果。"
                                )
                                probe_payload.pop("tools", None)
                                probe_payload["messages"] = [
                                    *probe_payload["messages"],
                                    {"role": "assistant", "content": blocks},
                                    {"role": "user", "content": result_prompt},
                                ]
                                payload = probe_payload
                                payload["stream"] = bool(provider["stream_enabled"])
                            else:
                                full = text_content
                                reasoning = "".join(str(block.get("thinking", "")) for block in blocks if block.get("type") == "thinking")
                                direct_answer = True
                        else:
                            message = data.get("choices", [{}])[0].get("message", {})
                            native_calls = message.get("tool_calls", [])
                            dsml_calls = parse_dsml_tool_calls(message.get("content") or "") if not native_calls else []
                            calls = [call for call in [*native_calls, *dsml_calls] if call.get("function", {}).get("name") in mcp_bindings]
                            if calls:
                                tool_messages = []
                                dsml_results = []
                                for call in calls[:8]:
                                    function = call.get("function", {})
                                    server, original = mcp_bindings[function["name"]]
                                    try:
                                        policy = expanded_mcp_server(server).get("tool_policies", {}).get(original, "allow")
                                        if policy == "ask":
                                            raise PermissionError("该工具设置为“每次询问”，当前未获得用户确认")
                                        arguments = json.loads(function.get("arguments") or "{}")
                                        if server.get("transport") == "builtin":
                                            arguments["_persona_key"] = motivation_key(body.persona_id)
                                            arguments["_conversation_id"] = body.conversation_id
                                            arguments["_source_message_id"] = user_id
                                        result = await invoke_server_tool(server, original, arguments)
                                        content = mcp_result_text(result)
                                        record_mcp_audit(server["id"], original, "success", conversation_id=body.conversation_id, user_message_id=user_id)
                                    except Exception as exc:
                                        content = f"MCP 工具调用失败：{exc}"
                                        record_mcp_audit(server["id"], original, "blocked" if isinstance(exc, PermissionError) else "error", str(exc), body.conversation_id, user_id)
                                    if call.get("_dsml"):
                                        dsml_results.append({"tool": function["name"], "result": content[:50000]})
                                    else:
                                        tool_messages.append({"role": "tool", "tool_call_id": call["id"], "content": content[:50000]})
                                probe_payload.pop("tools", None)
                                if dsml_results:
                                    result_prompt = (
                                        "<tool_results>\n"
                                        + "\n".join(json.dumps(item, ensure_ascii=False) for item in dsml_results)
                                        + "\n</tool_results>\n以上是宿主刚刚执行工具得到的真实结果。"
                                        "请根据结果直接回答用户，不要再次输出 DSML、工具调用代码或伪造结果。"
                                    )
                                    probe_payload["messages"] = [*probe_payload["messages"], message, {"role": "user", "content": result_prompt}]
                                else:
                                    probe_payload["messages"] = [*probe_payload["messages"], message, *tool_messages]
                                payload = probe_payload
                                payload["stream"] = bool(provider["stream_enabled"])
                            else:
                                full = message.get("content") or ""
                                reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
                                direct_answer = True
                        if direct_answer:
                            if reasoning:
                                yield json.dumps({"reasoning_delta": reasoning}, ensure_ascii=False) + "\n"
                            if full:
                                yield json.dumps({"delta": full}, ensure_ascii=False) + "\n"
                if not direct_answer:
                    async with client.stream("POST", url, headers=headers, json=payload) as response:
                        if response.status_code >= 400:
                            detail = (await response.aread()).decode("utf-8", "replace")[:500]
                            yield json.dumps({"error": f"API {response.status_code}: {detail}"}, ensure_ascii=False) + "\n"
                            return
                        if not provider["stream_enabled"]:
                            data = json.loads((await response.aread()).decode("utf-8", "replace"))
                            if provider["protocol"] == "anthropic":
                                full = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
                                reasoning = "".join(block.get("thinking", "") for block in data.get("content", []) if block.get("type") == "thinking")
                            else:
                                message = data.get("choices", [{}])[0].get("message", {})
                                full = message.get("content") or ""
                                reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
                            if reasoning:
                                yield json.dumps({"reasoning_delta": reasoning}, ensure_ascii=False) + "\n"
                            if full:
                                yield json.dumps({"delta": full}, ensure_ascii=False) + "\n"
                        else:
                            async for line in response.aiter_lines():
                                if not line.startswith("data:"):
                                    continue
                                raw = line[5:].strip()
                                if not raw or raw == "[DONE]":
                                    continue
                                try:
                                    event = json.loads(raw)
                                    if provider["protocol"] == "anthropic":
                                        event_delta = event.get("delta", {})
                                        delta = event_delta.get("text", "") if event.get("type") == "content_block_delta" else ""
                                        reasoning_delta = event_delta.get("thinking", "")
                                    else:
                                        choice_delta = event.get("choices", [{}])[0].get("delta", {})
                                        delta = choice_delta.get("content") or ""
                                        reasoning_delta = choice_delta.get("reasoning_content") or choice_delta.get("reasoning") or ""
                                except (json.JSONDecodeError, IndexError, TypeError):
                                    continue
                                if delta:
                                    full += delta
                                    yield json.dumps({"delta": delta}, ensure_ascii=False) + "\n"
                                if reasoning_delta:
                                    reasoning += reasoning_delta
                                    yield json.dumps({"reasoning_delta": reasoning_delta}, ensure_ascii=False) + "\n"
            if full:
                assistant_id = str(uuid.uuid4())
                generated_title = None
                with closing(db()) as connection:
                    connection.execute(
                        "INSERT INTO messages VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?, ?)",
                        (assistant_id, body.conversation_id, full, body.provider_id, provider["model"], now_iso(), reasoning, user_id),
                    )
                    connection.execute("INSERT OR REPLACE INTO message_selections VALUES (?,?,?)", (body.conversation_id, user_id, assistant_id))
                    conversation = connection.execute("SELECT title FROM conversations WHERE id=?", (body.conversation_id,)).fetchone()
                    mode_row = connection.execute("SELECT value FROM app_settings WHERE key='auto_title_mode'").fetchone()
                    title_mode = mode_row["value"] if mode_row else "local"
                    connection.commit()
                if not body.reuse_user_message_id and conversation and conversation["title"] == "新对话" and title_mode != "off":
                    generated_title = local_title(body.content)
                    if title_mode == "model":
                        try:
                            async with httpx.AsyncClient(timeout=60) as title_client:
                                generated_title = await model_title(title_client, provider, body.content, headers)
                        except Exception:
                            generated_title = local_title(body.content)
                    with closing(db()) as connection:
                        connection.execute("UPDATE conversations SET title=? WHERE id=?", (generated_title, body.conversation_id))
                        connection.commit()
                yield json.dumps({"done": True, "assistant_id": assistant_id, "user_id": user_id, "title": generated_title}, ensure_ascii=False) + "\n"
        except Exception as exc:
            yield json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


def roleplay_story_dict(connection: sqlite3.Connection, row: sqlite3.Row, include_turns: bool = False) -> dict[str, Any]:
    item = dict(row)
    item["cast"] = json.loads(item.pop("cast_json") or "[]")
    item["state"] = json.loads(item.pop("state_json") or "{}")
    if include_turns:
        item["turns"] = []
        for turn in connection.execute("SELECT * FROM roleplay_turns WHERE story_id=? ORDER BY turn_number", (row["id"],)):
            turn_item = dict(turn)
            turn_item["actor_drafts"] = json.loads(turn_item.pop("actor_drafts_json") or "[]")
            turn_item["checkpoint"] = json.loads(turn_item.pop("checkpoint_json") or "{}")
            item["turns"].append(turn_item)
    return item


def require_roleplay_provider(connection: sqlite3.Connection, provider_id: str) -> sqlite3.Row:
    provider = connection.execute("SELECT * FROM providers WHERE id=? AND enabled=1", (provider_id,)).fetchone()
    if not provider:
        raise HTTPException(422, "角色剧场使用的 AI 线路不存在或已停用")
    return provider


async def roleplay_model_once(provider: sqlite3.Row, system: str, prompt: str) -> str:
    headers = provider_headers(provider["protocol"], provider["api_key"], provider["custom_headers"])
    messages = [{"role": "user", "content": prompt}]
    if provider["protocol"] == "anthropic":
        payload = {
            "model": provider["model"], "max_tokens": provider["max_tokens"],
            "temperature": provider["temperature"], "top_p": provider["top_p"],
            "system": system, "messages": messages,
        }
    else:
        payload = {
            "model": provider["model"], "max_tokens": provider["max_tokens"],
            "temperature": provider["temperature"], "top_p": provider["top_p"],
            "messages": [{"role": "system", "content": system}, *messages],
        }
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            provider_endpoint(provider["base_url"], provider["protocol"]),
            headers=headers, json=payload,
        )
    if response.status_code >= 400:
        raise HTTPException(response.status_code, f"角色剧场线路返回 HTTP {response.status_code}：{response.text[:300]}")
    data = response.json()
    if provider["protocol"] == "anthropic":
        result = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
    else:
        result = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not str(result).strip():
        raise HTTPException(502, "角色剧场线路没有返回正文")
    return str(result).strip()


@app.get("/api/roleplay/stories")
def list_roleplay_stories() -> list[dict[str, Any]]:
    with closing(db()) as connection:
        rows = connection.execute("SELECT * FROM roleplay_stories ORDER BY updated_at DESC").fetchall()
        return [roleplay_story_dict(connection, row) for row in rows]


@app.post("/api/roleplay/stories")
def create_roleplay_story(body: RoleplayStoryIn) -> dict[str, Any]:
    story_id, created = str(uuid.uuid4()), now_iso()
    cast = [item.model_dump() for item in body.cast]
    with closing(db()) as connection:
        require_roleplay_provider(connection, body.narrator_provider_id)
        for actor in cast:
            require_roleplay_provider(connection, actor["provider_id"])
            if actor.get("persona_id") and not connection.execute("SELECT 1 FROM personas WHERE id=?", (actor["persona_id"],)).fetchone():
                raise HTTPException(422, f"角色“{actor['name']}”绑定的人格不存在")
        state = {
            "turn_number": 0, "scene": "尚未开场", "rolling_summary": "",
            "last_excerpt": "", "unresolved_hooks": [],
            "fictional_archive": True,
        }
        connection.execute(
            "INSERT INTO roleplay_stories VALUES (?,?,?,?,?,?,?,?,?,?)",
            (story_id, body.title, body.player_name, body.premise, body.narrator_provider_id,
             json.dumps(cast, ensure_ascii=False), json.dumps(state, ensure_ascii=False),
             "active", created, created),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM roleplay_stories WHERE id=?", (story_id,)).fetchone()
        return roleplay_story_dict(connection, row, True)


@app.get("/api/roleplay/stories/{story_id}")
def get_roleplay_story(story_id: str) -> dict[str, Any]:
    with closing(db()) as connection:
        row = connection.execute("SELECT * FROM roleplay_stories WHERE id=?", (story_id,)).fetchone()
        if not row:
            raise HTTPException(404, "剧场故事不存在")
        return roleplay_story_dict(connection, row, True)


@app.put("/api/roleplay/stories/{story_id}/state")
def update_roleplay_state(story_id: str, body: RoleplayStateIn) -> dict[str, Any]:
    with closing(db()) as connection:
        if not connection.execute("SELECT 1 FROM roleplay_stories WHERE id=?", (story_id,)).fetchone():
            raise HTTPException(404, "剧场故事不存在")
        connection.execute("UPDATE roleplay_stories SET status=?,updated_at=? WHERE id=?", (body.status, now_iso(), story_id))
        connection.commit()
        row = connection.execute("SELECT * FROM roleplay_stories WHERE id=?", (story_id,)).fetchone()
        return roleplay_story_dict(connection, row)


@app.post("/api/roleplay/stories/{story_id}/turns")
async def play_roleplay_turn(story_id: str, body: RoleplayTurnIn) -> dict[str, Any]:
    with closing(db()) as connection:
        row = connection.execute("SELECT * FROM roleplay_stories WHERE id=?", (story_id,)).fetchone()
        if not row:
            raise HTTPException(404, "剧场故事不存在")
        if row["status"] != "active":
            raise HTTPException(409, "这个故事已经收场；请先重新开启")
        story = roleplay_story_dict(connection, row, True)
        narrator = require_roleplay_provider(connection, row["narrator_provider_id"])
        actor_specs = []
        actor_providers = []
        for actor in story["cast"]:
            actor_specs.append(actor)
            actor_providers.append(require_roleplay_provider(connection, actor["provider_id"]))

    recent_turns = story["turns"][-12:]
    archive = "\n\n".join(
        f"第 {turn['turn_number']} 回合\n玩家输入：{turn['player_input']}\n小说正文：{turn['prose']}"
        for turn in recent_turns
    )
    shared_context = (
        f"故事：{story['title']}\n玩家角色姓名：{story['player_name']}\n"
        f"剧情设定：{story['premise']}\n既有剧情摘要：{story['state'].get('rolling_summary', '')}\n"
        f"最近正文：\n{archive or '尚未开场'}\n\n玩家这次明确输入：{body.player_input}"
    )

    async def actor_draft(actor: dict[str, Any], provider: sqlite3.Row) -> dict[str, str]:
        persona_prompt = ""
        if actor.get("persona_id"):
            with closing(db()) as connection:
                persona = connection.execute("SELECT prompt FROM personas WHERE id=?", (actor["persona_id"],)).fetchone()
                persona_prompt = persona["prompt"] if persona else ""
        system = (
            f"你只扮演虚构角色“{actor['name']}”。角色设定：{actor.get('description', '')}\n{persona_prompt}\n"
            "你独立判断该角色此刻可见的言行、情绪和意图，输出给旁白的角色提案，不写最终小说。\n"
            f"绝不能替玩家角色“{story['player_name']}”决定动作、台词、感受或内心；"
            "不得读取其他角色的隐藏想法，不得跳出角色评价用户。"
        )
        draft = await roleplay_model_once(provider, system, shared_context)
        return {"name": actor["name"], "draft": draft, "provider_id": actor["provider_id"]}

    drafts = await asyncio.gather(*(actor_draft(actor, provider) for actor, provider in zip(actor_specs, actor_providers)))
    draft_text = "\n\n".join(f"【{item['name']}的独立提案】\n{item['draft']}" for item in drafts)
    narrator_system = (
        "你是独立的小说旁白与场面调度者。请把角色提案编织成连贯、沉浸的中文小说正文并推进一小步剧情。"
        "可以描写环境、NPC与已列角色，但绝不能替玩家角色决定任何新动作、台词、感受、反应或内心；"
        "玩家角色只能执行玩家输入中明确写出的内容。不要出现“提案”“AI”“轮次”等幕后词。"
        "结尾必须留下玩家可以自由回应的空间。只输出小说正文。"
    )
    prose = await roleplay_model_once(narrator, narrator_system, f"{shared_context}\n\n{draft_text}")
    turn_number = int(story["state"].get("turn_number", 0)) + 1
    archive_system = (
        "你维护一份虚构小说的连续剧情档案。把旧档案与本回合正文合并为一份从开场延续至今的中文摘要，"
        "保留因果、人物关系变化、承诺、线索、未解决伏笔、地点和当前停场；不得把虚构事件写成现实。"
        "不评价玩家，不添加正文中不存在的事实。控制在 3000 字以内，只输出档案正文。"
    )
    summary = await roleplay_model_once(
        narrator, archive_system,
        f"旧档案：\n{story['state'].get('rolling_summary', '') or '尚无'}\n\n"
        f"第 {turn_number} 回合玩家输入：{body.player_input}\n本回合正文：\n{prose}",
    )
    checkpoint = {
        "turn_number": turn_number,
        "scene": prose[-600:],
        "last_player_input": body.player_input,
        "participating_characters": [item["name"] for item in drafts],
    }
    next_state = {
        **story["state"], "turn_number": turn_number, "scene": checkpoint["scene"],
        "last_excerpt": prose[-1200:], "rolling_summary": summary,
        "fictional_archive": True,
    }
    turn_id, created = str(uuid.uuid4()), now_iso()
    with closing(db()) as connection:
        connection.execute(
            "INSERT INTO roleplay_turns VALUES (?,?,?,?,?,?,?,?)",
            (turn_id, story_id, turn_number, body.player_input, json.dumps(drafts, ensure_ascii=False),
             prose, json.dumps(checkpoint, ensure_ascii=False), created),
        )
        connection.execute(
            "UPDATE roleplay_stories SET state_json=?,updated_at=? WHERE id=?",
            (json.dumps(next_state, ensure_ascii=False), created, story_id),
        )
        connection.commit()
    return {
        "id": turn_id, "story_id": story_id, "turn_number": turn_number,
        "player_input": body.player_input, "actor_drafts": drafts,
        "prose": prose, "checkpoint": checkpoint, "created_at": created,
    }


app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")


@app.get("/{path:path}")
def frontend(path: str = "") -> FileResponse:
    candidate = FRONTEND / path
    if path and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(FRONTEND / "index.html")
