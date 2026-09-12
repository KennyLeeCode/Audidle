"""Core domain enumerations.

These live in their own module because nearly every other layer imports them,
and keeping them here avoids circular imports between models and schemas.
"""

from enum import Enum


class Difficulty(str, Enum):
    """How recognizable the songs in a round are expected to be.

    Inherits from str so FastAPI and Pydantic serialize it as a plain string
    and so the values can be used directly as dictionary keys in config.
    """

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXPERT = "expert"
    IMPOSSIBLE = "impossible"


class GameStatus(str, Enum):
    """Server side lifecycle of a round.

    A persisted round is only ever PLAYING or REVEALING. The won and failed
    distinction is carried by RoundOutcome so that "which screen is this" and
    "how did it end" never get conflated.
    """

    PLAYING = "playing"
    REVEALING = "revealing"


class RoundOutcome(str, Enum):
    """How a finished round ended. None while the round is still playing."""

    WON = "won"
    FAILED = "failed"
    ABANDONED = "abandoned"


class SongStartMode(str, Enum):
    """Where in the track every clip for a round begins.

    Only FROM_START is implemented. MAIN_HOOK is declared now so the UI toggle
    and the round model already have a home for it.
    """

    FROM_START = "from_start"
    MAIN_HOOK = "main_hook"


class PlayableSourceKind(str, Enum):
    """How the browser should play a given audio source.

    FILE_URL sources are decodable, so the frontend can use Web Audio and hit
    exact sub second clip durations. SPOTIFY_SDK sources play inside Spotify's
    DRM player, which cannot offer that precision.
    """

    FILE_URL = "file_url"
    SPOTIFY_SDK = "spotify_sdk"
