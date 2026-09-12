"""Tests for the non negotiable game rules.

Each test maps to a stated rule. These exist because "the song must not change
mid round" is the kind of invariant that a refactor six months from now can
quietly break, and a comment will not catch that.
"""

import pytest

from app.config.stages import CLIP_STAGES, FINAL_STAGE_INDEX, STAGE_COUNT
from app.core.errors import (
    RoundAlreadyEndedError,
    RoundNotFoundError,
    RoundStillActiveError,
)
from app.models.enums import Difficulty, GameStatus, RoundOutcome

WRONG_TRACK_ID = "definitely-not-the-answer"


async def _start(game_service, difficulty=Difficulty.EASY):
    game_round, source = await game_service.start_round(difficulty)
    return game_round, source


# -- Rule 1 and 16: one song per round, rounds start at stage 0 -------------


@pytest.mark.anyio
async def test_round_starts_at_first_stage(game_service):
    game_round, _ = await _start(game_service)

    assert game_round.stage_index == 0
    assert game_round.clip_duration == CLIP_STAGES[0]
    assert game_round.status is GameStatus.PLAYING
    assert game_round.song_track_id


@pytest.mark.anyio
async def test_round_has_a_playable_source(game_service):
    """A round is never opened on a song the player cannot hear."""
    _, source = await _start(game_service)

    assert source is not None
    # Mock audio is decodable WAV, so exact sub second clipping is available.
    assert source.supports_precise_clips is True


# -- Rules 2 to 5: the song never changes ----------------------------------


@pytest.mark.anyio
async def test_song_never_changes_across_wrong_guesses(game_service):
    """The single most important rule in the game."""
    game_round, _ = await _start(game_service)
    original_song = game_round.song_track_id

    for _ in range(FINAL_STAGE_INDEX):
        result = await game_service.submit_guess(game_round.round_id, WRONG_TRACK_ID)
        assert result.correct is False
        assert result.game_round.song_track_id == original_song


@pytest.mark.anyio
async def test_song_never_changes_across_skips(game_service):
    game_round, _ = await _start(game_service)
    original_song = game_round.song_track_id

    for _ in range(FINAL_STAGE_INDEX):
        updated = await game_service.skip_stage(game_round.round_id)
        assert updated.song_track_id == original_song


@pytest.mark.anyio
async def test_song_never_changes_across_a_mixed_run(game_service):
    """The exact sequence from the specification: wrong, wrong, skip, wrong."""
    game_round, _ = await _start(game_service)
    original_song = game_round.song_track_id

    await game_service.submit_guess(game_round.round_id, WRONG_TRACK_ID)
    await game_service.submit_guess(game_round.round_id, WRONG_TRACK_ID)
    await game_service.skip_stage(game_round.round_id)
    result = await game_service.submit_guess(game_round.round_id, WRONG_TRACK_ID)

    assert result.game_round.song_track_id == original_song
    assert result.game_round.stage_index == 4
    assert result.game_round.clip_duration == CLIP_STAGES[4]


@pytest.mark.anyio
async def test_wrong_guess_advances_exactly_one_stage(game_service):
    game_round, _ = await _start(game_service)

    result = await game_service.submit_guess(game_round.round_id, WRONG_TRACK_ID)

    assert result.game_round.stage_index == 1
    assert result.game_round.clip_duration == CLIP_STAGES[1]
    assert result.round_ended is False


@pytest.mark.anyio
async def test_skip_advances_exactly_one_stage(game_service):
    game_round, _ = await _start(game_service)

    updated = await game_service.skip_stage(game_round.round_id)

    assert updated.stage_index == 1
    assert updated.status is GameStatus.PLAYING


# -- Rules 7 and 8: the final stage is playable, not an automatic loss ------


@pytest.mark.anyio
async def test_reaching_the_final_stage_does_not_end_the_round(game_service):
    """The player must actually get to hear the longest clip and guess at it."""
    game_round, _ = await _start(game_service)

    for _ in range(FINAL_STAGE_INDEX):
        await game_service.skip_stage(game_round.round_id)

    assert game_round.stage_index == FINAL_STAGE_INDEX
    assert game_round.clip_duration == CLIP_STAGES[-1]
    assert game_round.status is GameStatus.PLAYING
    assert game_round.is_active is True


@pytest.mark.anyio
async def test_correct_guess_on_the_final_stage_wins(game_service):
    game_round, _ = await _start(game_service)
    answer = game_round.song_track_id

    for _ in range(FINAL_STAGE_INDEX):
        await game_service.skip_stage(game_round.round_id)

    result = await game_service.submit_guess(game_round.round_id, answer)

    assert result.correct is True
    assert result.game_round.outcome is RoundOutcome.WON
    assert result.game_round.status is GameStatus.REVEALING


# -- Rule 9: wrong guess or skip on the final stage fails -------------------


@pytest.mark.anyio
async def test_wrong_guess_on_the_final_stage_fails(game_service):
    game_round, _ = await _start(game_service)
    for _ in range(FINAL_STAGE_INDEX):
        await game_service.skip_stage(game_round.round_id)

    result = await game_service.submit_guess(game_round.round_id, WRONG_TRACK_ID)

    assert result.correct is False
    assert result.round_ended is True
    assert result.game_round.outcome is RoundOutcome.FAILED
    # The stage index stays at the final stage rather than running off the end.
    assert result.game_round.stage_index == FINAL_STAGE_INDEX


@pytest.mark.anyio
async def test_skip_on_the_final_stage_fails(game_service):
    game_round, _ = await _start(game_service)
    for _ in range(FINAL_STAGE_INDEX):
        await game_service.skip_stage(game_round.round_id)

    updated = await game_service.skip_stage(game_round.round_id)

    assert updated.outcome is RoundOutcome.FAILED
    assert updated.status is GameStatus.REVEALING


# -- Rule 10: a correct guess wins at any stage -----------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("stage", range(STAGE_COUNT))
async def test_correct_guess_wins_at_every_stage(game_service, stage):
    game_round, _ = await _start(game_service)
    answer = game_round.song_track_id

    for _ in range(stage):
        await game_service.skip_stage(game_round.round_id)

    result = await game_service.submit_guess(game_round.round_id, answer)

    assert result.correct is True
    assert result.game_round.outcome is RoundOutcome.WON
    assert result.game_round.stages_used == stage + 1


# -- Rules 11 to 14: reveal, and nothing changes until the player moves on --


@pytest.mark.anyio
async def test_result_is_refused_while_the_round_is_active(game_service):
    """A player poking the result endpoint mid round gets a 409, not the answer."""
    game_round, _ = await _start(game_service)

    with pytest.raises(RoundStillActiveError):
        await game_service.get_result(game_round.round_id)


@pytest.mark.anyio
async def test_result_reveals_the_song_once_the_round_ends(game_service):
    game_round, _ = await _start(game_service)
    answer = game_round.song_track_id
    await game_service.submit_guess(game_round.round_id, answer)

    finished, song, source = await game_service.get_result(game_round.round_id)

    assert song.track_id == answer
    assert finished.outcome is RoundOutcome.WON
    # Full track playback on the reveal screen needs a source.
    assert source is not None


@pytest.mark.anyio
async def test_finished_rounds_reject_further_actions(game_service):
    """Covers double submits and replayed requests."""
    game_round, _ = await _start(game_service)
    await game_service.submit_guess(game_round.round_id, game_round.song_track_id)

    with pytest.raises(RoundAlreadyEndedError):
        await game_service.submit_guess(game_round.round_id, "anything")

    with pytest.raises(RoundAlreadyEndedError):
        await game_service.skip_stage(game_round.round_id)


@pytest.mark.anyio
async def test_starting_a_new_round_leaves_the_old_one_intact(game_service):
    """Rule 13 and 14: the finished round is not replaced or mutated."""
    first, _ = await _start(game_service)
    await game_service.submit_guess(first.round_id, first.song_track_id)
    first_answer = first.song_track_id

    second, _ = await _start(game_service)

    assert second.round_id != first.round_id
    assert second.stage_index == 0
    assert second.status is GameStatus.PLAYING

    reloaded, song, _ = await game_service.get_result(first.round_id)
    assert song.track_id == first_answer
    assert reloaded.status is GameStatus.REVEALING


# -- Reroll is distinct from skip ------------------------------------------


@pytest.mark.anyio
async def test_abandon_ends_the_round_without_counting_as_a_loss(game_service):
    game_round, _ = await _start(game_service)

    abandoned = await game_service.abandon_round(game_round.round_id)

    assert abandoned.outcome is RoundOutcome.ABANDONED
    assert abandoned.status is GameStatus.REVEALING


@pytest.mark.anyio
async def test_abandon_counts_as_a_loss_when_configured(song_service):
    from app.repositories.round_repository import InMemoryRoundRepository
    from app.services.game_service import GameService
    from app.services.recent_songs import RecentSongsTracker

    service = GameService(
        song_service=song_service,
        round_repository=InMemoryRoundRepository(),
        recent_songs=RecentSongsTracker(),
        reroll_counts_as_loss=True,
    )
    game_round, _ = await service.start_round(Difficulty.EASY)

    abandoned = await service.abandon_round(game_round.round_id)

    assert abandoned.outcome is RoundOutcome.FAILED


# -- Error handling ---------------------------------------------------------


@pytest.mark.anyio
async def test_unknown_round_raises(game_service):
    with pytest.raises(RoundNotFoundError):
        await game_service.get_round("no-such-round")


@pytest.mark.anyio
async def test_guessing_an_unknown_track_is_a_normal_wrong_guess(game_service):
    """An id that is not in the catalog must not crash the round."""
    game_round, _ = await _start(game_service)

    result = await game_service.submit_guess(game_round.round_id, "not-a-real-id")

    assert result.correct is False
    assert result.guessed_track is None
    assert result.game_round.stage_index == 1


@pytest.mark.anyio
@pytest.mark.parametrize("difficulty", list(Difficulty))
async def test_every_difficulty_can_open_a_round(game_service, difficulty):
    """Guards against a tier having no eligible or no playable songs."""
    game_round, source = await game_service.start_round(difficulty)

    assert game_round.difficulty is difficulty
    assert source is not None
