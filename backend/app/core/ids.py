"""Identifier generation.

Round ids are unguessable on purpose. A sequential or predictable id would let
a player enumerate other rounds, and more importantly would let them poke the
result endpoint for a round they have not finished.
"""

import secrets

ROUND_ID_BYTES = 12


def new_round_id() -> str:
    """Return a URL safe, cryptographically random round id."""
    return secrets.token_urlsafe(ROUND_ID_BYTES)


def new_session_id() -> str:
    """Return an anonymous session id used to scope recent song history."""
    return secrets.token_urlsafe(ROUND_ID_BYTES)
