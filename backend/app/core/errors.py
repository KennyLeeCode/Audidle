"""Domain errors and their HTTP mapping.

Services raise these instead of raising HTTPException directly. That keeps the
service layer free of web framework concerns and testable without a client,
while main.py installs one handler that turns any AudidleError into a
consistent JSON error body.
"""

from http import HTTPStatus


class AudidleError(Exception):
    """Base class for every expected failure in the application."""

    status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# -- Round lifecycle --------------------------------------------------------


class RoundNotFoundError(AudidleError):
    """The round id does not exist, or has expired out of the store."""

    status_code = HTTPStatus.NOT_FOUND
    code = "round_not_found"


class RoundAlreadyEndedError(AudidleError):
    """A guess, skip, or stage action arrived for a finished round.

    Covers double submits and replayed requests, which would otherwise let a
    player advance stages on a round that is already revealed.
    """

    status_code = HTTPStatus.CONFLICT
    code = "round_already_ended"


class RoundStillActiveError(AudidleError):
    """Someone asked for the answer while the round was still playing."""

    status_code = HTTPStatus.CONFLICT
    code = "round_still_active"


class DuplicateGuessError(AudidleError):
    """The same track was already guessed in this round."""

    status_code = HTTPStatus.CONFLICT
    code = "duplicate_guess"


# -- Catalog and selection --------------------------------------------------


class SongNotFoundError(AudidleError):
    """A track id is not present in the active catalog."""

    status_code = HTTPStatus.NOT_FOUND
    code = "song_not_found"


class NoEligibleSongError(AudidleError):
    """No song matched the requested difficulty and filters."""

    status_code = HTTPStatus.NOT_FOUND
    code = "no_eligible_song"


class NoPlayableSongError(AudidleError):
    """Songs matched, but none of them had a usable audio source.

    Raised only after exhausting max_song_selection_attempts, so the player is
    never handed a round it is impossible to hear.
    """

    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "no_playable_song"


# -- Upstream providers -----------------------------------------------------


class CatalogUnavailableError(AudidleError):
    """The upstream catalog (for example Spotify) could not be reached."""

    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "catalog_unavailable"


class CatalogRateLimitedError(AudidleError):
    """The upstream catalog returned a rate limit response."""

    status_code = HTTPStatus.TOO_MANY_REQUESTS
    code = "catalog_rate_limited"

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ProviderConfigurationError(AudidleError):
    """A provider was selected but is missing required configuration."""

    status_code = HTTPStatus.INTERNAL_SERVER_ERROR
    code = "provider_misconfigured"
