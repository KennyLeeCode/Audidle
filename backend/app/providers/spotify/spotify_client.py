"""Low level Spotify Web API client.

The only module in the application that speaks HTTP to Spotify. Everything above
it deals in domain objects, which is what keeps Spotify specific concerns such
as token refresh, rate limit headers, and pagination from leaking outward.

Responsibilities, all of them plumbing rather than game logic:

  - Client Credentials authentication, with the token cached until it expires.
  - Rate limit handling, honouring the Retry-After header Spotify sends on 429.
  - Retry with backoff on transient upstream failures.
  - Mapping HTTP failures onto the application's own error types.

Credentials are read from settings, which read them from the environment. They
are never logged and never returned in any response.
"""

import asyncio
import base64
import logging
import time
from typing import Any

import httpx

from app.core.errors import (
    CatalogRateLimitedError,
    CatalogUnavailableError,
    ProviderConfigurationError,
)

logger = logging.getLogger(__name__)

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

# Refresh slightly before the real expiry, so a request cannot race a token
# that expires between the check and the call.
TOKEN_EXPIRY_MARGIN_SECONDS = 30

MAX_RETRIES = 3
# Cap on how long a rate limit response is allowed to block a request. Spotify
# can return very long Retry-After values, and a player waiting 90 seconds for
# autocomplete is worse than a failed search.
MAX_RETRY_AFTER_SECONDS = 5.0


class SpotifyClient:
    """Authenticated HTTP client for the Spotify Web API."""

    def __init__(self, client_id: str, client_secret: str, timeout: float = 10.0) -> None:
        if not client_id or not client_secret:
            raise ProviderConfigurationError(
                "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set to use the "
                "Spotify provider"
            )
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.AsyncClient(timeout=timeout)

        self._token: str | None = None
        self._token_expires_at = 0.0
        # Without this lock, a burst of concurrent requests on a cold cache
        # would each independently fetch a token.
        self._token_lock = asyncio.Lock()

    # -- Authentication -------------------------------------------------------

    async def _get_token(self) -> str:
        """Return a valid access token, fetching a new one only when needed."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token

        async with self._token_lock:
            # Re-check inside the lock. Another caller may have refreshed while
            # this one was waiting.
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token

            credentials = f"{self._client_id}:{self._client_secret}".encode()
            header = base64.b64encode(credentials).decode()

            try:
                response = await self._http.post(
                    TOKEN_URL,
                    headers={"Authorization": f"Basic {header}"},
                    data={"grant_type": "client_credentials"},
                )
            except httpx.HTTPError as error:
                raise CatalogUnavailableError(
                    "Could not reach Spotify for authentication"
                ) from error

            if response.status_code != 200:
                # Deliberately does not echo the response body, which can carry
                # the client id back.
                raise ProviderConfigurationError(
                    f"Spotify rejected the client credentials (status {response.status_code})"
                )

            payload = response.json()
            self._token = payload["access_token"]
            self._token_expires_at = (
                time.monotonic() + payload.get("expires_in", 3600) - TOKEN_EXPIRY_MARGIN_SECONDS
            )
            logger.info("refreshed the Spotify access token")
            return self._token

    # -- Requests -------------------------------------------------------------

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a Spotify endpoint, retrying transient failures.

        Raises:
            CatalogRateLimitedError: rate limited beyond what is worth waiting for.
            CatalogUnavailableError: unreachable, or failing after retries.
        """
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            token = await self._get_token()

            try:
                response = await self._http.get(
                    f"{API_BASE}/{path.lstrip('/')}",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as error:
                last_error = error
                await self._backoff(attempt)
                continue

            if response.status_code == 200:
                return response.json()

            if response.status_code == 401:
                # Token rejected despite being unexpired. Force a refresh and
                # retry once rather than failing the request.
                logger.warning("Spotify rejected the access token, refreshing")
                self._token = None
                continue

            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", "1"))
                if retry_after > MAX_RETRY_AFTER_SECONDS or attempt == MAX_RETRIES - 1:
                    raise CatalogRateLimitedError(
                        "Spotify is rate limiting requests, try again shortly",
                        retry_after_seconds=retry_after,
                    )
                logger.warning("rate limited by Spotify, waiting %.1fs", retry_after)
                await asyncio.sleep(retry_after)
                continue

            if response.status_code == 404:
                # A genuine "not here", not a failure. Callers translate this
                # into None rather than an error.
                return {}

            if response.status_code == 403:
                # Almost always an endpoint this app's credentials cannot reach.
                # Worth naming explicitly, because the cause is a Spotify policy
                # change rather than anything wrong with the request.
                raise CatalogUnavailableError(
                    f"Spotify denied access to '{path}'. This app's credentials may not "
                    f"have access to that endpoint"
                )

            if response.status_code >= 500:
                last_error = CatalogUnavailableError(f"Spotify returned {response.status_code}")
                await self._backoff(attempt)
                continue

            raise CatalogUnavailableError(
                f"Spotify request to '{path}' failed with {response.status_code}"
            )

        raise CatalogUnavailableError(
            "Spotify is not responding after several attempts"
        ) from last_error

    @staticmethod
    async def _backoff(attempt: int) -> None:
        """Exponential backoff between retries."""
        await asyncio.sleep(0.25 * (2**attempt))

    async def aclose(self) -> None:
        """Close the underlying connection pool. Called on app shutdown."""
        await self._http.aclose()
