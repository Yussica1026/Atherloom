from __future__ import annotations

from backend.game.gm_port import GameMasterPort
from backend.game.models import PlayerActionIn
from backend.game.proposals import (
    TurnContext,
    materialize_turn_proposal,
    parse_turn_proposal,
    validate_turn_proposal_basis,
)
from backend.game.repository import GameRepository
from backend.game.rules import validate_player_event_authority


class LongWorldTurnService:
    """Application service that keeps model calls outside SQLite transactions."""

    def __init__(self, repository: GameRepository, game_master: GameMasterPort):
        self.repository = repository
        self.game_master = game_master

    async def run_player_turn(self, session_id: str, action: PlayerActionIn) -> dict[str, object]:
        trusted_action = PlayerActionIn.model_validate_json(action.model_dump_json())
        prepared = self.repository.prepare_player_action(session_id, trusted_action)
        if prepared.committed is not None:
            return prepared.committed
        if prepared.state is None or prepared.state_hash is None:
            raise RuntimeError("Player turn preflight returned no state")

        trusted_context = TurnContext(
            session_id=session_id,
            based_on_revision=prepared.state.revision,
            based_on_state_hash=prepared.state_hash,
            action=trusted_action,
            state=prepared.state,
        )
        # A provider adapter may mutate its input accidentally.  Give it a deep
        # copy and retain the trusted basis used for authorization and commit.
        provider_context = TurnContext.model_validate_json(trusted_context.model_dump_json())
        raw_proposal = await self.game_master.propose_turn(provider_context)
        proposal = parse_turn_proposal(raw_proposal)
        validate_turn_proposal_basis(trusted_context, proposal)
        events = materialize_turn_proposal(proposal)
        validate_player_event_authority(trusted_context.state, trusted_context.action, events)
        return self.repository.commit_player_events(
            session_id,
            trusted_context.action,
            trusted_context.based_on_state_hash,
            events,
            proposal.narration,
        )
