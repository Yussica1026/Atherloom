"""Lightweight casual games domain.

This package owns its persistence and rules independently from both the legacy
``/api/games`` feature and the LongWorld engine.
"""

from .models import (
    ActionCommitIn,
    BehaviorConfig,
    BehaviorConfigUpdate,
    CasualGameHistoryEntry,
    CasualGameResult,
    CasualGameResultRecord,
    CasualGameSession,
    SessionCreateIn,
)
from .repository import CasualGameRepository
from .schema import init_schema

__all__ = [
    "ActionCommitIn",
    "BehaviorConfig",
    "BehaviorConfigUpdate",
    "CasualGameHistoryEntry",
    "CasualGameRepository",
    "CasualGameResult",
    "CasualGameResultRecord",
    "CasualGameSession",
    "SessionCreateIn",
    "init_schema",
]
