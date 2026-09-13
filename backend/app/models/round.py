"""The GameRound domain model.

This is the most rule-critical type in the project. It holds the answer, and it
enforces the single most important invariant in the game:

    The song chosen for a round is written once, at construction, and is never
    reassigned for the lifetime of the round.

Wrong guesses, skips, and stage unlocks mutate stage_index only. There is no
setter, method, or code path here that can swap the song out.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.config.stages import FINAL_STAGE_INDEX, clip_duration_for_stage
from app.models.enums import Difficulty, GameStatus, RoundOutcome, SongStartMode


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Guess:
    """One submitted guess, kept for the reveal summary and duplicate checks."""

    song_id: str
    correct: bool
    stage_index: int
    submitted_at: datetime = field(default_factory=_utc_now)


@dataclass
class GameRound:
    """Server side state for one round of Audidle.

    Only ever exists on the backend. The wire schemas in app/schemas/game.py
    decide what subset of this is safe to send to the browser, and while the
    round is playing that subset excludes anything identifying the song.
    """

    round_id: str
    # THE ANSWER. Assigned at construction and never reassigned. See module
    # docstring. Guarded by test_song_never_changes in the test suite.
    song_id: str
    difficulty: Difficulty
    session_id: str | None = None
    stage_index: int = 0
    status: GameStatus = GameStatus.PLAYING
    outcome: RoundOutcome | None = None
    start_mode: SongStartMode = SongStartMode.FROM_START
    # Offset into the track that every clip in this round starts from. Always 0
    # for FROM_START. Reserved for the future Main Hook mode.
    start_offset_ms: int = 0
    guesses: list[Guess] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utc_now)
    ended_at: datetime | None = None

    # -- Derived views ------------------------------------------------------

    @property
    def clip_duration(self) -> float:
        """Seconds of audio currently unlocked."""
        return clip_duration_for_stage(self.stage_index)

    @property
    def is_final_stage(self) -> bool:
        """Whether a wrong guess or skip from here ends the round."""
        return self.stage_index >= FINAL_STAGE_INDEX

    @property
    def is_active(self) -> bool:
        return self.status is GameStatus.PLAYING

    @property
    def stages_used(self) -> int:
        """How many stages the player consumed, counted from one."""
        return self.stage_index + 1

    def has_guessed(self, song_id: str) -> bool:
        """Whether this track was already submitted in this round."""
        return any(guess.song_id == song_id for guess in self.guesses)

    # -- Mutations ----------------------------------------------------------
    #
    # Note what is absent: there is no method that sets song_id. Stage
    # progression touches stage_index and nothing else.

    def record_guess(self, song_id: str, correct: bool) -> Guess:
        """Append a guess at the current stage and return it."""
        guess = Guess(song_id=song_id, correct=correct, stage_index=self.stage_index)
        self.guesses.append(guess)
        return guess

    def advance_stage(self) -> None:
        """Unlock the next clip length of the same song.

        Raises:
            RuntimeError: if called on the final stage. Callers must check
                is_final_stage first and fail the round instead.
        """
        if self.is_final_stage:
            raise RuntimeError("cannot advance past the final stage, fail the round instead")
        self.stage_index += 1

    def finish(self, outcome: RoundOutcome) -> None:
        """Close the round and move it into the reveal state."""
        self.status = GameStatus.REVEALING
        self.outcome = outcome
        self.ended_at = _utc_now()
