"""Recently played song tracking.

Keeps a short per session history so a new round does not immediately hand back
a song the player just heard. Best effort by design: if excluding the history
would leave no eligible songs at all, SongService relaxes the exclusion rather
than failing the round. A repeat is a far better outcome than a dead end.
"""

from collections import OrderedDict, deque


class RecentSongsTracker:
    """Bounded recent history, keyed by anonymous session id.

    Owns nothing but this history. Deliberately not folded into GameService,
    which is already the most rule-dense class in the project.
    """

    def __init__(self, history_size: int = 30, max_sessions: int = 5_000) -> None:
        self._history_size = history_size
        self._max_sessions = max_sessions
        # Ordered so the least recently used session can be evicted, which
        # stops an unbounded number of sessions leaking memory.
        self._sessions: OrderedDict[str, deque[str]] = OrderedDict()

    def record(self, session_id: str, track_id: str) -> None:
        """Mark a track as just played for a session."""
        history = self._sessions.get(session_id)
        if history is None:
            history = deque(maxlen=self._history_size)
            self._sessions[session_id] = history
        elif track_id in history:
            # Re-record so the track moves to the most recent position.
            history.remove(track_id)

        history.append(track_id)
        self._sessions.move_to_end(session_id)
        self._evict_if_needed()

    def get(self, session_id: str | None) -> frozenset[str]:
        """Return the track ids recently played by a session."""
        if session_id is None:
            return frozenset()
        return frozenset(self._sessions.get(session_id, ()))

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _evict_if_needed(self) -> None:
        while len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)
