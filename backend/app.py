from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.motivation import DRIVES, EVENTS, apply_event, context_summary, default_state, normalize, tick

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
              prompt_cache INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS personas (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL,
              created_at TEXT NOT NULL
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
              state_json TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS game_saves (
              game_id TEXT NOT NULL, persona_key TEXT NOT NULL, state_json TEXT NOT NULL,
              updated_at TEXT NOT NULL, PRIMARY KEY(game_id, persona_key)
            );
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(providers)")}
        if "custom_headers" not in columns:
            connection.execute("ALTER TABLE providers ADD COLUMN custom_headers TEXT NOT NULL DEFAULT '{}'")
        if "prompt_cache" not in columns:
            connection.execute("ALTER TABLE providers ADD COLUMN prompt_cache INTEGER NOT NULL DEFAULT 1")
        message_columns = {row["name"] for row in connection.execute("PRAGMA table_info(messages)")}
        if "reasoning" not in message_columns:
            connection.execute("ALTER TABLE messages ADD COLUMN reasoning TEXT NOT NULL DEFAULT ''")
        if "parent_message_id" not in message_columns:
            connection.execute("ALTER TABLE messages ADD COLUMN parent_message_id TEXT")
        conversation_columns = {row["name"] for row in connection.execute("PRAGMA table_info(conversations)")}
        for column in ("pinned", "starred", "archived"):
            if column not in conversation_columns:
                connection.execute(f"ALTER TABLE conversations ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
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


class PersonaIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    prompt: str = ""


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
    tool_permissions: dict[str, str] = Field(default_factory=lambda: {
        "web_search": "allow", "memory_read": "allow", "memory_write": "ask",
        "diary_write": "ask", "delete": "ask"
    })
    font_scale: int = Field(default=100, ge=85, le=130)
    message_density: str = Field(default="comfortable", pattern="^(compact|comfortable|relaxed)$")
    code_theme: str = Field(default="auto", pattern="^(auto|light|dark|contrast)$")
    memory_strategy: str = Field(default="hybrid", pattern="^(local_first|hybrid|remote_first)$")


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


class ChatIn(BaseModel):
    conversation_id: str
    content: str = Field(min_length=1)
    provider_id: str
    persona_id: str | None = None
    reuse_user_message_id: str | None = None


class MotivationEventIn(BaseModel):
    event: str


class MotivationEnabledIn(BaseModel):
    enabled: bool


class GameActionIn(BaseModel):
    action: str
    amount: int = Field(default=1, ge=1, le=20)
    target: str = ""


app = FastAPI(title="Local Claude Style Client", docs_url=None, redoc_url=None)


@app.on_event("startup")
def startup() -> None:
    init_db()


def masked_provider(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["prompt_cache"] = bool(item["prompt_cache"])
    item["has_api_key"] = bool(item.pop("api_key"))
    return item


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    with closing(db()) as connection:
        providers = [masked_provider(row) for row in connection.execute("SELECT * FROM providers ORDER BY created_at")]
        personas = [dict(row) for row in connection.execute("SELECT * FROM personas ORDER BY created_at")]
        conversations = [dict(row) for row in connection.execute("SELECT * FROM conversations ORDER BY updated_at DESC")]
        settings_rows = {row["key"]: row["value"] for row in connection.execute("SELECT * FROM app_settings")}
    return {"providers": providers, "personas": personas, "conversations": conversations, "settings": {
        "auto_title_mode": settings_rows.get("auto_title_mode", "local"),
        "title_provider_id": settings_rows.get("title_provider_id", ""),
        "summary_enabled": settings_rows.get("summary_enabled", "true") == "true",
        "summary_trigger_rounds": int(settings_rows.get("summary_trigger_rounds", "24")),
        "summary_prompt": settings_rows.get("summary_prompt", DEFAULT_SUMMARY_PROMPT),
        "default_summary_prompt": DEFAULT_SUMMARY_PROMPT,
        "display_name": settings_rows.get("display_name", ""),
        "tool_permissions": json.loads(settings_rows.get("tool_permissions", '{"web_search":"allow","memory_read":"allow","memory_write":"ask","diary_write":"ask","delete":"ask"}')),
        "font_scale": int(settings_rows.get("font_scale", "100")),
        "message_density": settings_rows.get("message_density", "comfortable"),
        "code_theme": settings_rows.get("code_theme", "auto"),
        "memory_strategy": settings_rows.get("memory_strategy", "hybrid"),
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
            "tool_permissions": json.dumps(body.tool_permissions, ensure_ascii=False),
            "font_scale": str(body.font_scale),
            "message_density": body.message_density,
            "code_theme": body.code_theme,
            "memory_strategy": body.memory_strategy,
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
        connection.execute(
            "INSERT INTO providers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (provider_id, body.name, protocol, body.base_url.rstrip("/"), body.api_key, body.model, int(body.enabled), body.custom_headers, int(body.prompt_cache), now_iso()),
        )
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
        connection.commit()
    return {"id": persona_id, "name": body.name, "prompt": body.prompt, "created_at": created}


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


@app.post("/api/conversations/{conversation_id}/branch/{message_id}")
def branch_conversation(conversation_id: str, message_id: str) -> dict[str, Any]:
    with closing(db()) as connection:
        source = connection.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        pivot = connection.execute("SELECT * FROM messages WHERE id=? AND conversation_id=?", (message_id, conversation_id)).fetchone()
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
            "SELECT * FROM messages WHERE conversation_id=? AND created_at<=? ORDER BY created_at", (conversation_id, pivot["created_at"])
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
        return [dict(row) for row in connection.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (conversation_id,))]


def motivation_key(persona_id: str | None) -> str:
    return persona_id or "__default__"


FISHING_WATERS = {
    "willow_bay": {"name": "柳湾", "unlock": 0, "fish": [("银尾鲫", 8, 62), ("青纹鲈", 18, 28), ("月斑鳜", 55, 10)]},
    "mist_lake": {"name": "雾湖", "unlock": 220, "fish": [("雾鳞鱼", 22, 55), ("琉璃鳟", 46, 32), ("星灯鲤", 120, 13)]},
    "cloud_coast": {"name": "云海岸", "unlock": 620, "fish": [("风翼鲷", 50, 52), ("潮鸣鲭", 95, 34), ("极光鳐", 260, 14)]},
}


def default_fishing_state() -> dict[str, Any]:
    return {"coins": 120, "bait": 8, "water": "willow_bay", "turn": 0, "catch": {}, "journal": [], "unlocked": ["willow_bay"]}


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
        {"id": "claw_machine", "name": "抓娃娃机", "icon": "◇", "status": "coming", "description": "观察、移动、瞄准和下爪；将接入我们的可视化物理玩法。"},
        {"id": "text_arcade", "name": "文字街机", "icon": "✦", "status": "adapter", "description": "虚拟筹码街机入口；等待外部游戏许可与适配。"},
    ]


def load_game(connection: sqlite3.Connection, game_id: str, persona_id: str | None) -> dict[str, Any]:
    row = connection.execute("SELECT state_json FROM game_saves WHERE game_id=? AND persona_key=?", (game_id, motivation_key(persona_id))).fetchone()
    if row:
        return json.loads(row["state_json"])
    if game_id == "quiet_fishing":
        return default_fishing_state()
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
    if game_id != "quiet_fishing":
        raise HTTPException(409, "游戏尚未开放")
    with closing(db()) as connection:
        state = load_game(connection, game_id, persona_id)
        events: list[str] = []
        if body.action == "cast":
            count = min(body.amount, state["bait"])
            if count < 1:
                raise HTTPException(409, "鱼饵用完了")
            for _ in range(count):
                state["bait"] -= 1; state["turn"] += 1
                name, value = fishing_pick(state)
                state["catch"][name] = state["catch"].get(name, 0) + 1
                events.append(f"钓到了{name}，价值 {value} 枚云贝")
        elif body.action == "buy_bait":
            cost = body.amount * 5
            if state["coins"] < cost:
                raise HTTPException(409, "云贝不够")
            state["coins"] -= cost; state["bait"] += body.amount; events.append(f"买了 {body.amount} 份鱼饵")
        elif body.action == "sell_all":
            values = {name: value for water in FISHING_WATERS.values() for name, value, _ in water["fish"]}
            income = sum(values.get(name, 0) * count for name, count in state["catch"].items())
            state["coins"] += income; state["catch"] = {}; events.append(f"渔获卖出，得到 {income} 枚云贝")
        elif body.action == "travel":
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


def load_motivation(connection: sqlite3.Connection, persona_id: str | None) -> tuple[bool, dict[str, Any]]:
    row = connection.execute("SELECT * FROM motivation_states WHERE persona_key=?", (motivation_key(persona_id),)).fetchone()
    if not row:
        return False, default_state()
    return bool(row["enabled"]), normalize(json.loads(row["state_json"]))


def save_motivation(connection: sqlite3.Connection, persona_id: str | None, enabled: bool, state: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO motivation_states(persona_key,enabled,state_json,updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(persona_key) DO UPDATE SET enabled=excluded.enabled,state_json=excluded.state_json,updated_at=excluded.updated_at",
        (motivation_key(persona_id), int(enabled), json.dumps(normalize(state), ensure_ascii=False), now_iso()),
    )


@app.get("/api/motivation/{persona_key}")
def get_motivation(persona_key: str) -> dict[str, Any]:
    persona_id = None if persona_key == "__default__" else persona_key
    with closing(db()) as connection:
        enabled, state = load_motivation(connection, persona_id)
    return {"enabled": enabled, "state": state, "drives": DRIVES, "events": list(EVENTS)}


@app.put("/api/motivation/{persona_key}/enabled")
def set_motivation_enabled(persona_key: str, body: MotivationEnabledIn) -> dict[str, Any]:
    persona_id = None if persona_key == "__default__" else persona_key
    with closing(db()) as connection:
        _, state = load_motivation(connection, persona_id)
        save_motivation(connection, persona_id, body.enabled, state)
        connection.commit()
    return {"enabled": body.enabled, "state": state}


@app.post("/api/motivation/{persona_key}/event")
def motivation_event(persona_key: str, body: MotivationEventIn) -> dict[str, Any]:
    if body.event not in EVENTS:
        raise HTTPException(422, "未知的动机事件")
    persona_id = None if persona_key == "__default__" else persona_key
    with closing(db()) as connection:
        enabled, state = load_motivation(connection, persona_id)
        changes = apply_event(state, body.event)
        save_motivation(connection, persona_id, enabled, state)
        connection.commit()
    return {"enabled": enabled, "state": state, "changes": changes}


@app.post("/api/motivation/{persona_key}/tick")
def motivation_tick(persona_key: str) -> dict[str, Any]:
    persona_id = None if persona_key == "__default__" else persona_key
    with closing(db()) as connection:
        enabled, state = load_motivation(connection, persona_id)
        result = tick(state)
        save_motivation(connection, persona_id, enabled, result["state"])
        connection.commit()
    return {"enabled": enabled, **result}


def load_chat_context(connection: sqlite3.Connection, body: ChatIn, cutoff: str | None = None) -> tuple[sqlite3.Row, str, list[dict[str, str]]]:
    provider = connection.execute("SELECT * FROM providers WHERE id=? AND enabled=1", (body.provider_id,)).fetchone()
    if not provider:
        raise HTTPException(404, "API 配置不存在或已停用")
    persona_prompt = ""
    if body.persona_id:
        persona = connection.execute("SELECT * FROM personas WHERE id=?", (body.persona_id,)).fetchone()
        persona_prompt = persona["prompt"] if persona else ""
    conversation = connection.execute("SELECT * FROM conversations WHERE id=?", (body.conversation_id,)).fetchone()
    if not conversation:
        raise HTTPException(404, "会话不存在")
    query = "SELECT role, content FROM messages WHERE conversation_id=?"
    params: list[Any] = [body.conversation_id]
    if cutoff:
        query += " AND created_at<=?"
        params.append(cutoff)
    query += " ORDER BY created_at"
    messages = [{"role": row["role"], "content": row["content"]} for row in connection.execute(query, params)]
    system_parts = [part for part in (persona_prompt, conversation["summary"]) if part]
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


@app.post("/api/providers/test")
async def test_provider(body: ProviderIn) -> dict[str, Any]:
    protocol = body.protocol
    signature = f"{body.base_url} {body.model}".lower()
    if protocol == "openai" and "deepseek" in signature:
        protocol = "deepseek"
    elif protocol == "openai" and ("bigmodel.cn" in signature or body.model.lower().startswith("glm-")):
        protocol = "glm"
    headers = provider_headers(protocol, body.api_key, body.custom_headers)
    url = provider_models_endpoint(body.base_url, protocol)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(502, f"无法连接：{exc}") from exc
    if response.status_code == 404:
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


def retrieve_memories(connection: sqlite3.Connection, query: str, limit: int = 5) -> list[dict[str, Any]]:
    query_terms = text_bigrams(query)
    if not query_terms:
        return []
    ranked = []
    for row in connection.execute("SELECT * FROM memories WHERE archived=0 AND deleted_at IS NULL"):
        terms = text_bigrams(f"{row['title']} {row['content']}")
        overlap = len(query_terms & terms)
        if overlap:
            score = overlap / max(1, len(query_terms)) + (0.15 if row["starred"] else 0)
            ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [{"id": row["id"], "title": row["title"], "kind": row["kind"], "content": row["content"]} for _, row in ranked[:limit]]


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
        permission_row = connection.execute("SELECT value FROM app_settings WHERE key='tool_permissions'").fetchone()
        permissions = json.loads(permission_row["value"]) if permission_row else {"memory_read": "allow"}
        memory_sources = retrieve_memories(connection, body.content) if permissions.get("memory_read") == "allow" else []
        if memory_sources:
            memory_context = "<relevant_memories>\n" + "\n\n".join(
                f"[memory:{item['id']}] {item['title']}\n{item['content']}" for item in memory_sources
            ) + "\n</relevant_memories>"
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] += "\n\n" + memory_context
            else:
                messages.insert(0, {"role": "system", "content": memory_context})
        motivation_enabled, motivation_state = load_motivation(connection, body.persona_id)
        if motivation_enabled:
            apply_event(motivation_state, "contact_message")
            motivation_result = tick(motivation_state)
            motivation_context = context_summary(motivation_result["state"])
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] += "\n\n" + motivation_context
            else:
                messages.insert(0, {"role": "system", "content": motivation_context})
            save_motivation(connection, body.persona_id, True, motivation_result["state"])
        if not body.reuse_user_message_id:
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, 'user', ?, ?, ?, ?, '', NULL)",
                (user_id, body.conversation_id, body.content, body.provider_id, provider["model"], created),
            )
        connection.execute("UPDATE conversations SET provider_id=?, persona_id=?, updated_at=? WHERE id=?", (body.provider_id, body.persona_id, created, body.conversation_id))
        connection.commit()
    messages.append({"role": "user", "content": body.content})

    async def stream():
        full = ""
        reasoning = ""
        try:
            if memory_sources:
                yield json.dumps({"memory_sources": [{"id": item["id"], "title": item["title"], "kind": item["kind"]} for item in memory_sources]}, ensure_ascii=False) + "\n"
            async with httpx.AsyncClient(timeout=180) as client:
                if provider["protocol"] == "anthropic":
                    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
                    payload = {"model": provider["model"], "max_tokens": 4096, "stream": True, "messages": [m for m in messages if m["role"] != "system"]}
                    if system:
                        payload["system"] = [{"type": "text", "text": system, **({"cache_control": {"type": "ephemeral"}} if provider["prompt_cache"] else {})}]
                    headers = {"x-api-key": provider["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"}
                    url = provider_endpoint(provider["base_url"], "anthropic")
                else:
                    payload = {"model": provider["model"], "stream": True, "messages": messages}
                    if provider["protocol"] in ("deepseek", "glm"):
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
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode("utf-8", "replace")[:500]
                        yield json.dumps({"error": f"API {response.status_code}: {detail}"}, ensure_ascii=False) + "\n"
                        return
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


app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")


@app.get("/{path:path}")
def frontend(path: str = "") -> FileResponse:
    candidate = FRONTEND / path
    if path and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(FRONTEND / "index.html")
