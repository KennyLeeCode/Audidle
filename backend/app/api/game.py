"""Game routes.

HTTP concerns only. Every route here parses a request, calls exactly one
GameService method, and maps the result into a schema. No game rule lives in
this file, which is why it stays short even though it is the busiest router.
"""

from fastapi import APIRouter, Response, status

from app.config.stages import STAGE_COUNT
from app.core.errors import SongNotFoundError
from app.dependencies import GameServiceDep, SongServiceDep
from app.models.round import GameRound
from app.models.song import PlayableSource, SongSelectionCriteria
from app.schemas.game import (
    GuessResponse,
    RoundResultResponse,
    RoundStateResponse,
    StartRoundRequest,
    SubmitGuessRequest,
)
from app.schemas.song import AudioSourceResponse, SongDetail

router = APIRouter(prefix="/game", tags=["game"])


def _audio_url(round_id: str) -> str:
    """Round scoped, opaque URL for this round's audio.

    Deliberately not the underlying filename. A URL like /audio/mock012.wav in
    the network tab would leak the answer just as effectively as putting the
    title in the JSON, and would be a much easier leak to miss.
    """
    return f"/api/game/{round_id}/audio"


def _round_state(game_round: GameRound, source: PlayableSource | None) -> RoundStateResponse:
    audio = (
        AudioSourceResponse.from_domain(source, url=_audio_url(game_round.round_id))
        if source is not None
        else None
    )
    return RoundStateResponse.from_domain(game_round, total_stages=STAGE_COUNT, audio=audio)


@router.post(
    "/round",
    response_model=RoundStateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a new round",
)
async def start_round(request: StartRoundRequest, games: GameServiceDep) -> RoundStateResponse:
    """Draw a song for the difficulty and open a round on it.

    The response carries no information about which song was chosen.
    """
    # Only the player's filters are assembled here. Recent song exclusion is
    # merged in by GameService, which owns that history.
    criteria = SongSelectionCriteria(
        difficulty=request.difficulty,
        genres=tuple(request.genres) if request.genres else None,
        decades=tuple(request.decades) if request.decades else None,
        explicit=request.explicit,
    )

    game_round, source = await games.start_round(
        difficulty=request.difficulty,
        session_id=request.session_id,
        criteria=criteria,
        start_mode=request.start_mode,
    )
    return _round_state(game_round, source)


@router.get(
    "/{round_id}",
    response_model=RoundStateResponse,
    summary="Read the current state of a round",
)
async def get_round(
    round_id: str, games: GameServiceDep, songs: SongServiceDep
) -> RoundStateResponse:
    """Re-read a round, for example after a page refresh.

    Safe to call at any time. While the round is playing the payload still
    contains no answer data.
    """
    game_round = await games.get_round(round_id)
    source = await songs.get_playable_source(game_round.song_track_id)
    return _round_state(game_round, source)


@router.post(
    "/{round_id}/guess",
    response_model=GuessResponse,
    summary="Submit a guess",
)
async def submit_guess(
    round_id: str, request: SubmitGuessRequest, games: GameServiceDep, songs: SongServiceDep
) -> GuessResponse:
    """Check a guess server side.

    The backend is authoritative here. The client reports which track the
    player picked, never whether that pick was right.
    """
    result = await games.submit_guess(round_id, request.track_id)
    source = await songs.get_playable_source(result.game_round.song_track_id)

    return GuessResponse(
        correct=result.correct,
        round_ended=result.round_ended,
        round=_round_state(result.game_round, source),
        guessed_track=(
            SongDetail.from_domain(result.guessed_track) if result.guessed_track else None
        ),
    )


@router.post(
    "/{round_id}/skip",
    response_model=RoundStateResponse,
    summary="Skip the current guess attempt",
)
async def skip_stage(
    round_id: str, games: GameServiceDep, songs: SongServiceDep
) -> RoundStateResponse:
    """Unlock the next clip length of the same song.

    This does not change the song. On the final stage it ends the round.
    """
    game_round = await games.skip_stage(round_id)
    source = await songs.get_playable_source(game_round.song_track_id)
    return _round_state(game_round, source)


@router.post(
    "/{round_id}/abandon",
    response_model=RoundStateResponse,
    summary="Abandon the round, for a reroll onto a different song",
)
async def abandon_round(
    round_id: str, games: GameServiceDep, songs: SongServiceDep
) -> RoundStateResponse:
    """Close a round the player chose to walk away from.

    Distinct from skip. The client follows this with a fresh POST /round.
    """
    game_round = await games.abandon_round(round_id)
    source = await songs.get_playable_source(game_round.song_track_id)
    return _round_state(game_round, source)


@router.get(
    "/{round_id}/result",
    response_model=RoundResultResponse,
    summary="Reveal the answer for a finished round",
)
async def get_result(round_id: str, games: GameServiceDep) -> RoundResultResponse:
    """Return the song, but only once the round has ended.

    Called while the round is still playing, this returns 409 rather than the
    answer. That guard lives in GameService.get_result.
    """
    game_round, song, source = await games.get_result(round_id)
    return RoundResultResponse.from_domain(
        game_round,
        song,
        total_stages=STAGE_COUNT,
        source=source,
        audio_url=_audio_url(round_id),
    )


@router.get(
    "/{round_id}/audio",
    summary="Stream this round's audio",
    response_class=Response,
)
async def get_round_audio(
    round_id: str, games: GameServiceDep, songs: SongServiceDep
) -> Response:
    """Serve the round's audio behind an opaque, round scoped URL.

    Routing audio through the round rather than exposing a filename is what
    stops the network tab from giving the answer away. The round id is required
    and unguessable, so this is no easier to enumerate than the round itself.

    The full file is served in both clip and full playback modes. Trimming
    happens client side, because only the browser can schedule a clip boundary
    precisely enough for the short stages, and because re-requesting audio on
    every stage would make replaying a clip feel sluggish.
    """
    game_round = await games.get_round(round_id)
    stream = await songs.open_stream(game_round.song_track_id)
    if stream is None:
        raise SongNotFoundError("no audio stream is available for this round")

    payload, media_type = stream
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            # Cacheable by the browser for the life of the round so replaying a
            # clip does not refetch. Private, since the URL is round scoped.
            "Cache-Control": "private, max-age=3600",
            "Accept-Ranges": "bytes",
        },
    )
