"""The Audidle round state machine.

This is the authoritative owner of every game rule. The API layer does HTTP and
nothing else, the frontend renders and plays audio and nothing else, and every
decision about whether a guess was right, whether a stage advances, and whether
a round is over is made here.

The rules it enforces, stated once so they are not scattered across the file:

  1. One song is drawn per round, at round creation.
  2. That song never changes for the life of the round.
  3. A wrong guess advances the stage. It does not change the song.
  4. A skip advances the stage. It does not change the song.
  5. Reaching the final stage does not fail the player. They hear that clip and
     get one real guess at it.
  6. A wrong guess or a skip while already on the final stage fails the round.
  7. A correct guess at any stage wins immediately.
  8. A finished round moves to REVEALING and stays there until the player
     starts a new round.

Note what this module does not contain: any call to pick a song outside
start_round. Stage progression goes through _advance_or_fail, which touches
stage_index and nothing else.
"""

import logging

from app.core.errors import (
    RoundAlreadyEndedError,
    RoundNotFoundError,
    RoundStillActiveError,
    SongNotFoundError,
)
from app.core.ids import new_round_id
from app.models.enums import Difficulty, GameStatus, RoundOutcome, SongStartMode
from app.models.round import GameRound
from app.models.song import PlayableSource, Song, SongSelectionCriteria
from app.repositories.round_repository import RoundRepository
from app.services.recent_songs import RecentSongsTracker
from app.services.song_service import SongService

logger = logging.getLogger(__name__)


class GuessResult:
    """Outcome of one guess, as the API layer needs it.

    A small result object rather than a tuple, because the caller needs three
    separate facts: was it correct, did the round end, and what stage is the
    player on now.
    """

    def __init__(self, game_round: GameRound, correct: bool, guessed_track: Song | None) -> None:
        self.game_round = game_round
        self.correct = correct
        self.guessed_track = guessed_track

    @property
    def round_ended(self) -> bool:
        return self.game_round.status is GameStatus.REVEALING


class GameService:
    """Creates rounds, validates guesses, and advances or ends rounds."""

    def __init__(
        self,
        song_service: SongService,
        round_repository: RoundRepository,
        recent_songs: RecentSongsTracker,
        reroll_counts_as_loss: bool = False,
    ) -> None:
        self._songs = song_service
        self._rounds = round_repository
        self._recent = recent_songs
        self._reroll_counts_as_loss = reroll_counts_as_loss

    # -- Round creation -----------------------------------------------------

    async def start_round(
        self,
        difficulty: Difficulty,
        session_id: str | None = None,
        criteria: SongSelectionCriteria | None = None,
        start_mode: SongStartMode = SongStartMode.FROM_START,
    ) -> tuple[GameRound, PlayableSource]:
        """Draw a song and open a new round on it.

        This is the ONLY place in the application that selects a song. If a
        future change ever makes a song appear to swap mid round, this is the
        only method that could have caused it.

        Callers pass criteria carrying the player's filters. Recent song
        exclusion is merged in here rather than by the caller, because this
        service owns the history and the caller should not have to know it
        exists.
        """
        selection = self._with_recent_exclusions(
            criteria or SongSelectionCriteria(difficulty=difficulty),
            session_id,
        )

        song, source = await self._songs.pick_playable_song(selection)

        game_round = GameRound(
            round_id=new_round_id(),
            song_track_id=song.track_id,
            difficulty=difficulty,
            session_id=session_id,
            start_mode=start_mode,
            # Every clip in this round starts here. Always 0 for FROM_START,
            # which is the only mode implemented.
            start_offset_ms=0,
        )

        await self._rounds.save(game_round)
        if session_id:
            self._recent.record(session_id, song.track_id)

        logger.info(
            "round %s opened on difficulty %s at stage 0",
            game_round.round_id,
            difficulty.value,
        )
        return game_round, source

    # -- Round lookup -------------------------------------------------------

    async def get_round(self, round_id: str) -> GameRound:
        """Fetch a round or raise. Used by every action below."""
        game_round = await self._rounds.get(round_id)
        if game_round is None:
            raise RoundNotFoundError("round not found or expired")
        return game_round

    async def _get_active_round(self, round_id: str) -> GameRound:
        """Fetch a round that is still accepting actions.

        Rejecting finished rounds here is what makes double submits and
        replayed requests safe. Without it, a resent guess could advance the
        stage of a round that is already revealed.
        """
        game_round = await self.get_round(round_id)
        if not game_round.is_active:
            raise RoundAlreadyEndedError("round has already ended")
        return game_round

    # -- Player actions -----------------------------------------------------

    async def submit_guess(self, round_id: str, track_id: str) -> GuessResult:
        """Validate a guess against the round's hidden answer.

        Comparison is on track id, never on title text. That is what keeps
        "Blinding Lights" and "Blinding Lights - Single Version" from being
        conflated, and it is why the frontend submits a chosen search result
        rather than a free text string.
        """
        game_round = await self._get_active_round(round_id)

        correct = track_id == game_round.song_track_id
        game_round.record_guess(track_id, correct)

        if correct:
            game_round.finish(RoundOutcome.WON)
            logger.info("round %s won at stage %s", game_round.round_id, game_round.stage_index)
        else:
            self._advance_or_fail(game_round)

        await self._rounds.save(game_round)

        # Looked up after the comparison, so an unknown track id is still a
        # normal wrong guess rather than an error.
        guessed_track = await self._songs.get_song(track_id)
        return GuessResult(game_round, correct, guessed_track)

    async def skip_stage(self, round_id: str) -> GameRound:
        """Give up the current guess attempt and unlock more of the same song.

        Skip is not "next song". It behaves exactly like a wrong guess: the
        stage advances, the song does not change, and on the final stage it
        ends the round as a loss.
        """
        game_round = await self._get_active_round(round_id)
        self._advance_or_fail(game_round)
        await self._rounds.save(game_round)
        return game_round

    async def abandon_round(self, round_id: str) -> GameRound:
        """End a round because the player rerolled onto a different song.

        Distinct from skip. Skip surrenders one attempt at the current song,
        reroll throws the whole round away. Whether that counts as a loss is
        configurable via REROLL_COUNTS_AS_LOSS.
        """
        game_round = await self.get_round(round_id)
        if game_round.is_active:
            outcome = (
                RoundOutcome.FAILED if self._reroll_counts_as_loss else RoundOutcome.ABANDONED
            )
            game_round.finish(outcome)
            await self._rounds.save(game_round)
        return game_round

    # -- Reveal -------------------------------------------------------------

    async def get_result(self, round_id: str) -> tuple[GameRound, Song, PlayableSource | None]:
        """Return the answer, but only for a round that has actually ended.

        This is the one path that exposes the song, so the guard below is the
        most important line in it. A player poking this mid round gets a 409,
        not the answer.
        """
        game_round = await self.get_round(round_id)
        if game_round.is_active:
            raise RoundStillActiveError("round is still playing, the answer is not available yet")

        song = await self._songs.get_song(game_round.song_track_id)
        if song is None:
            raise SongNotFoundError("the song for this round is no longer in the catalog")

        source = await self._songs.get_playable_source(game_round.song_track_id)
        return game_round, song, source

    # -- Internal -----------------------------------------------------------

    def _with_recent_exclusions(
        self, criteria: SongSelectionCriteria, session_id: str | None
    ) -> SongSelectionCriteria:
        """Return the criteria with this session's recent songs excluded."""
        recent = self._recent.get(session_id)
        if not recent:
            return criteria
        return SongSelectionCriteria(
            difficulty=criteria.difficulty,
            exclude_track_ids=criteria.exclude_track_ids | recent,
            genres=criteria.genres,
            decades=criteria.decades,
            release_year_min=criteria.release_year_min,
            release_year_max=criteria.release_year_max,
            explicit=criteria.explicit,
        )

    def _advance_or_fail(self, game_round: GameRound) -> None:
        """Shared progression rule for wrong guesses and skips.

        Both paths route through here so the "final stage ends the round" rule
        exists in exactly one place and cannot drift between them.
        """
        if game_round.is_final_stage:
            game_round.finish(RoundOutcome.FAILED)
            logger.info("round %s failed on the final stage", game_round.round_id)
            return

        game_round.advance_stage()
        logger.debug(
            "round %s advanced to stage %s (%ss), same song",
            game_round.round_id,
            game_round.stage_index,
            game_round.clip_duration,
        )
