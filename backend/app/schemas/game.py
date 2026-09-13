"""Wire schemas for the game API.

The single most important property of this module: RoundStateResponse, the
payload returned while a round is still in progress, contains no field that
could identify the song. No title, no artist, no album, no artwork, no external
link, and no filename. A player with devtools open sees exactly what a player
without them sees.

The answer appears in exactly one schema, RoundResultResponse, which is only
ever produced for a round that has already ended.
"""

from pydantic import BaseModel, Field

from app.models.enums import Difficulty, GameStatus, RoundOutcome, SongStartMode
from app.models.round import GameRound
from app.models.song import PlayableSource, Song
from app.schemas.song import AudioSourceResponse, SongDetail


class StartRoundRequest(BaseModel):
    """Body for opening a round.

    Filters are accepted now even though only difficulty is wired up, so the
    client contract does not have to change when genre and decade land.
    """

    difficulty: Difficulty
    session_id: str | None = Field(
        default=None,
        description="Anonymous client id used to avoid repeating recent songs",
    )
    start_mode: SongStartMode = SongStartMode.FROM_START
    genres: list[str] | None = None
    decades: list[int] | None = None
    explicit: bool | None = None


class RoundStateResponse(BaseModel):
    """The gameplay view of a round. Contains no answer data.

    clip_duration is sent alongside stage_index so the client never has to
    compute it, and so a change to the stage ladder takes effect immediately
    without a frontend deploy.
    """

    round_id: str
    difficulty: Difficulty
    stage_index: int
    clip_duration: float = Field(description="Seconds of audio currently unlocked")
    total_stages: int
    is_final_stage: bool
    status: GameStatus
    guess_count: int
    start_offset_ms: int = Field(description="Offset every clip in this round starts from")
    audio: AudioSourceResponse | None = None

    @classmethod
    def from_domain(
        cls,
        game_round: GameRound,
        total_stages: int,
        audio: AudioSourceResponse | None = None,
    ) -> "RoundStateResponse":
        return cls(
            round_id=game_round.round_id,
            difficulty=game_round.difficulty,
            stage_index=game_round.stage_index,
            clip_duration=game_round.clip_duration,
            total_stages=total_stages,
            is_final_stage=game_round.is_final_stage,
            status=game_round.status,
            guess_count=len(game_round.guesses),
            start_offset_ms=game_round.start_offset_ms,
            audio=audio,
        )


class SubmitGuessRequest(BaseModel):
    """Body for a guess.

    A provider and that provider's id, exactly as the search result carried
    them. The client never sees or sends an Audidle song id, so the payload
    gives away nothing about which songs are in our catalog.

    Still a chosen result rather than free text, which is what makes identity
    comparison possible instead of string matching.
    """

    provider: str = Field(min_length=1, description="Provider the result came from")
    external_id: str = Field(min_length=1, description="That provider's id for the song")


class GuessResponse(BaseModel):
    """Result of one guess.

    guessed_track echoes back what the player picked so the UI can list their
    wrong attempts. It is the guess, never the answer.
    """

    correct: bool
    round_ended: bool
    round: RoundStateResponse
    guessed_track: SongDetail | None = None


class RoundResultResponse(BaseModel):
    """The reveal. The only schema in the application that carries the answer.

    Produced exclusively for rounds whose status is already REVEALING. The
    guard lives in GameService.get_result.
    """

    round_id: str
    difficulty: Difficulty
    outcome: RoundOutcome
    won: bool
    stages_used: int = Field(description="How many stages the player consumed, counted from one")
    final_stage_index: int
    duration_reached: float = Field(description="Longest clip length the player unlocked")
    total_stages: int
    guess_count: int
    song: SongDetail
    audio: AudioSourceResponse | None = Field(
        default=None, description="Source for full track playback on the reveal screen"
    )

    @classmethod
    def from_domain(
        cls,
        game_round: GameRound,
        song: Song,
        total_stages: int,
        source: PlayableSource | None,
        audio_url: str | None,
    ) -> "RoundResultResponse":
        return cls(
            round_id=game_round.round_id,
            difficulty=game_round.difficulty,
            outcome=game_round.outcome or RoundOutcome.ABANDONED,
            won=game_round.outcome is RoundOutcome.WON,
            stages_used=game_round.stages_used,
            final_stage_index=game_round.stage_index,
            duration_reached=game_round.clip_duration,
            total_stages=total_stages,
            guess_count=len(game_round.guesses),
            song=SongDetail.from_domain(song),
            audio=(
                AudioSourceResponse.from_domain(source, url=audio_url)
                if source is not None
                else None
            ),
        )


class ErrorResponse(BaseModel):
    """Consistent error body for every AudidleError."""

    code: str
    message: str
