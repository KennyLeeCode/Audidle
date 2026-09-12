"""Persistence for rounds.

Rounds hold the answer, so where they live matters. The interface exists so the
in memory store used in development can be replaced by a SQL or Redis backed
store for deployment without GameService changing at all.
"""

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta

from app.models.round import GameRound

# How long a round survives before it is swept. Long enough that a player can
# sit on the reveal screen listening to the full track, short enough that
# abandoned rounds do not accumulate forever in memory.
ROUND_TTL = timedelta(hours=2)


class RoundRepository(ABC):
    """Storage port for rounds."""

    @abstractmethod
    async def save(self, game_round: GameRound) -> None:
        """Insert or update a round."""

    @abstractmethod
    async def get(self, round_id: str) -> GameRound | None:
        """Fetch a round by id, or None if unknown or expired."""

    @abstractmethod
    async def delete(self, round_id: str) -> None:
        """Remove a round."""


class InMemoryRoundRepository(RoundRepository):
    """Process local round store.

    Fine for a single process development server. Not suitable for multiple
    workers, since round state would not be shared between them. That is the
    reason the interface exists.
    """

    def __init__(self, ttl: timedelta = ROUND_TTL) -> None:
        self._rounds: dict[str, GameRound] = {}
        self._ttl = ttl

    async def save(self, game_round: GameRound) -> None:
        self._sweep()
        self._rounds[game_round.round_id] = game_round

    async def get(self, round_id: str) -> GameRound | None:
        game_round = self._rounds.get(round_id)
        if game_round is None:
            return None
        if self._is_expired(game_round):
            del self._rounds[round_id]
            return None
        return game_round

    async def delete(self, round_id: str) -> None:
        self._rounds.pop(round_id, None)

    def _is_expired(self, game_round: GameRound) -> bool:
        return datetime.now(UTC) - game_round.created_at > self._ttl

    def _sweep(self) -> None:
        """Drop expired rounds. Cheap enough to run on every write."""
        expired = [
            round_id
            for round_id, game_round in self._rounds.items()
            if self._is_expired(game_round)
        ]
        for round_id in expired:
            del self._rounds[round_id]
