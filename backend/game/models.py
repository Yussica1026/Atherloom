from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ENTITY_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_default=True,
        validate_assignment=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class TurnNarration(StrictModel):
    """Non-authoritative prose returned by the GM/Narrator for one turn."""

    text: str = Field(min_length=1, max_length=12_000)

    @field_validator("text", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class PlayerRef(StrictModel):
    kind: Literal["player"] = "player"
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


class AICharacterRef(StrictModel):
    kind: Literal["ai_character"] = "ai_character"
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


class NPCRef(StrictModel):
    kind: Literal["npc"] = "npc"
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


ActorRef: TypeAlias = Annotated[
    PlayerRef | AICharacterRef | NPCRef,
    Field(discriminator="kind"),
]


class LocationPosition(StrictModel):
    kind: Literal["location"] = "location"
    location_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


class ActorPosition(StrictModel):
    kind: Literal["actor"] = "actor"
    actor: ActorRef


class RemovedPosition(StrictModel):
    kind: Literal["removed"] = "removed"


ItemPosition: TypeAlias = Annotated[
    LocationPosition | ActorPosition | RemovedPosition,
    Field(discriminator="kind"),
]


class LocationDefinition(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    exits: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def exits_are_unique(self) -> "LocationDefinition":
        if len(self.exits) != len(set(self.exits)):
            raise ValueError(f"地点 {self.id} 的出口不能重复")
        return self


class ItemDefinition(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    initial_location_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    quantity: int = Field(default=1, ge=1, le=1_000_000)


class NPCDefinition(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    initial_location_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


class FactDefinition(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    text: str = Field(min_length=1, max_length=2000)
    initially_known_by_player: bool = False


class QuestDefinition(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=4000)
    initial_status: Literal["available", "active", "hidden"] = "available"


class WorldCreateIn(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=8000)
    starting_location_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    locations: list[LocationDefinition] = Field(min_length=1, max_length=500)
    items: list[ItemDefinition] = Field(default_factory=list, max_length=2000)
    npcs: list[NPCDefinition] = Field(default_factory=list, max_length=1000)
    facts: list[FactDefinition] = Field(default_factory=list, max_length=2000)
    quests: list[QuestDefinition] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def references_are_valid(self) -> "WorldCreateIn":
        location_ids = [location.id for location in self.locations]
        if len(location_ids) != len(set(location_ids)):
            raise ValueError("地点 ID 不能重复")
        known_locations = set(location_ids)
        if self.starting_location_id not in known_locations:
            raise ValueError("初始地点不存在")
        for location in self.locations:
            unknown = [target for target in location.exits if target not in known_locations]
            if unknown:
                raise ValueError(f"地点 {location.id} 包含不存在的出口：{', '.join(unknown)}")
        for label, values in (
            ("物品", [item.id for item in self.items]),
            ("NPC", [npc.id for npc in self.npcs]),
            ("事实", [fact.id for fact in self.facts]),
            ("任务", [quest.id for quest in self.quests]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} ID 不能重复")
        for item in self.items:
            if item.initial_location_id not in known_locations:
                raise ValueError(f"物品 {item.id} 的初始地点不存在")
        for npc in self.npcs:
            if npc.initial_location_id not in known_locations:
                raise ValueError(f"NPC {npc.id} 的初始地点不存在")
        return self


class AICharacterSeedIn(StrictModel):
    """Session-local identity for one independent AI resident.

    Persona/provider contents are resolved and snapshotted by the server.  The
    client may select existing IDs, but cannot inject prompts, credentials, or
    provider transport configuration through this command.
    """

    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=120)
    persona_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    provider_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    initial_location_id: str | None = Field(
        default=None,
        max_length=120,
        pattern=ENTITY_ID_PATTERN,
    )
    active: bool = True


class AICharacterPersonaSnapshot(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(default="", max_length=32_000)


class AICharacterProviderSnapshot(StrictModel):
    """Non-secret provider behavior frozen for a game session."""

    name: str = Field(min_length=1, max_length=120)
    protocol: Literal["openai", "anthropic", "deepseek", "glm"]
    model: str = Field(min_length=1, max_length=240)
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=1.0, ge=0, le=1)
    max_tokens: int = Field(default=4096, ge=1, le=200_000)
    stream_enabled: bool = True
    thinking_enabled: bool = True


class AICharacterResolvedRuntime(StrictModel):
    """Trusted result of resolving a seed against server-owned libraries."""

    persona_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    provider_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    persona: AICharacterPersonaSnapshot
    provider: AICharacterProviderSnapshot


class SessionCreateIn(StrictModel):
    world_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    player_display_name: str = Field(min_length=1, max_length=120)
    player_id: str | None = Field(default=None, max_length=120, pattern=ENTITY_ID_PATTERN)
    branch_name: str = Field(default="主线", min_length=1, max_length=120)
    ai_characters: list[AICharacterSeedIn] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def ai_character_ids_are_unique(self) -> "SessionCreateIn":
        actor_ids = [actor.id for actor in self.ai_characters]
        if len(actor_ids) != len(set(actor_ids)):
            raise ValueError("AICharacter ID 不能重复")
        if self.player_id is not None and self.player_id in set(actor_ids):
            raise ValueError("Player 与 AICharacter 的 ID 不能重复")
        return self


class PlayerState(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=120)
    location_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


class AICharacterState(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=120)
    persona_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    provider_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    runtime_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    location_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    active: bool = True


class NPCState(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    location_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    alive: bool = True


class LocationState(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    exits: list[str] = Field(default_factory=list, max_length=100)
    visited: bool = False


class ItemState(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    quantity: int = Field(default=1, ge=1, le=1_000_000)
    position: ItemPosition


class RelationshipState(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    subject: ActorRef
    object: ActorRef
    affinity: int = Field(default=0, ge=-100, le=100)
    trust: int = Field(default=0, ge=-100, le=100)
    fear: int = Field(default=0, ge=0, le=100)
    respect: int = Field(default=0, ge=-100, le=100)


class ConditionState(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    name: str = Field(min_length=1, max_length=160)
    subject: ActorRef
    active: bool = True


class QuestState(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=4000)
    status: Literal["hidden", "available", "active", "completed", "failed"] = "available"


class OpenThreadState(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    title: str = Field(min_length=1, max_length=240)
    status: Literal["open", "resolved", "abandoned"] = "open"


class FactState(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    text: str = Field(min_length=1, max_length=2000)
    known_by: list[ActorRef] = Field(default_factory=list, max_length=1000)


class PhoneMessageState(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    sender: ActorRef
    content: str = Field(min_length=1, max_length=4000)
    world_minute: int = Field(ge=0)


class PhoneThreadState(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    participants: list[ActorRef] = Field(min_length=2, max_length=20)
    messages: list[PhoneMessageState] = Field(default_factory=list, max_length=10000)


class WorldClock(StrictModel):
    total_minutes: int = Field(default=0, ge=0)


class WorldState(StrictModel):
    schema_version: Literal[1] = 1
    world_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    world_version_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    session_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    revision: int = Field(default=0, ge=0)
    turn: int = Field(default=0, ge=0)
    clock: WorldClock = Field(default_factory=WorldClock)
    player: PlayerState
    ai_characters: dict[str, AICharacterState] = Field(default_factory=dict)
    npcs: dict[str, NPCState] = Field(default_factory=dict)
    locations: dict[str, LocationState]
    items: dict[str, ItemState] = Field(default_factory=dict)
    relationships: dict[str, RelationshipState] = Field(default_factory=dict)
    conditions: dict[str, ConditionState] = Field(default_factory=dict)
    quests: dict[str, QuestState] = Field(default_factory=dict)
    open_threads: dict[str, OpenThreadState] = Field(default_factory=dict)
    facts: dict[str, FactState] = Field(default_factory=dict)
    phone_threads: dict[str, PhoneThreadState] = Field(default_factory=dict)


class MoveIntent(StrictModel):
    type: Literal["move"] = "move"
    destination_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


class TakeItemIntent(StrictModel):
    type: Literal["take_item"] = "take_item"
    item_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


class DropItemIntent(StrictModel):
    type: Literal["drop_item"] = "drop_item"
    item_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


class WaitIntent(StrictModel):
    type: Literal["wait"] = "wait"
    minutes: int = Field(ge=1, le=1440)


class HelpActorIntent(StrictModel):
    type: Literal["help_actor"] = "help_actor"
    target: ActorRef


class AcceptQuestIntent(StrictModel):
    type: Literal["accept_quest"] = "accept_quest"
    quest_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


class FreeformIntent(StrictModel):
    type: Literal["freeform"] = "freeform"


PlayerIntent: TypeAlias = Annotated[
    MoveIntent
    | TakeItemIntent
    | DropItemIntent
    | WaitIntent
    | HelpActorIntent
    | AcceptQuestIntent
    | FreeformIntent,
    Field(discriminator="type"),
]


class PlayerActionIn(StrictModel):
    actor: PlayerRef
    intent: PlayerIntent
    content: str = Field(default="", max_length=4000)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def freeform_requires_content(self) -> "PlayerActionIn":
        if isinstance(self.intent, FreeformIntent) and not self.content.strip():
            raise ValueError("自由行动必须包含非空正文")
        return self


AICharacterIntent: TypeAlias = Annotated[
    MoveIntent | TakeItemIntent | DropItemIntent | HelpActorIntent,
    Field(discriminator="type"),
]


class AICharacterActionIn(StrictModel):
    actor: AICharacterRef
    intent: AICharacterIntent
    content: str = Field(default="", max_length=4000)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=160)


TurnActionIn: TypeAlias = PlayerActionIn | AICharacterActionIn


class SaveCreateIn(StrictModel):
    name: str = Field(default="手动存档", min_length=1, max_length=120)
    revision: int | None = Field(default=None, ge=0)


class BranchCreateIn(StrictModel):
    branch_name: str = Field(min_length=1, max_length=120)
