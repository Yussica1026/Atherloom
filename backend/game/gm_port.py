from __future__ import annotations

from typing import Protocol

from backend.game.proposals import TurnContext


class GameMasterPort(Protocol):
    """Model-independent boundary for a future Game Master adapter."""

    async def propose_turn(self, context: TurnContext) -> object:
        """Return an untrusted structured proposal; never receive a repository."""

        ...


class GameMasterError(RuntimeError):
    """Base class for failures at the model-independent GM boundary."""


class GameMasterConfigurationError(GameMasterError):
    """The requested GM provider is missing, disabled, or misconfigured."""


class GameMasterTransportError(GameMasterError):
    """The GM provider could not be reached or returned an HTTP failure."""


class GameMasterResponseError(GameMasterError):
    """The GM provider returned content that cannot be used as a proposal."""
