"""Catalog discovery.

Discovery answers "which songs should Audidle know about" and nothing else. It
is kept apart from popularity on purpose: a source that is good at telling you a
recording exists is usually bad at telling you how famous it is, and conflating
the two is how a catalog ends up with fabricated difficulty.

So a SongCandidate carries identity and, at most, whatever the source genuinely
measured. It never carries a stream estimate, and the pipeline is content to
create a song with no popularity at all. Such a song sits in the catalog,
ineligible, until a popularity provider has something real to say about it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SongCandidate:
    """A song a discovery source thinks we should know about.

    Only `title` and `artist` are required. Everything else is filled in when
    the source happens to know it, and left empty otherwise rather than guessed.
    """

    title: str
    artist: str
    isrc: str | None = None
    musicbrainz_recording_id: str | None = None
    musicbrainz_artist_id: str | None = None
    release_year: int | None = None
    # Which provider produced this candidate, and its own id for it. Kept so a
    # bad source can be traced and revoked later.
    source: str = "unknown"
    source_id: str | None = None

    @property
    def label(self) -> str:
        return f"{self.title} - {self.artist}"

    @property
    def has_strong_identity(self) -> bool:
        """Whether this can be deduplicated without falling back to text."""
        return bool(self.musicbrainz_recording_id or self.isrc)


class CatalogSeedProvider(ABC):
    """A source of candidate songs."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier recorded on everything this source produces."""

    @abstractmethod
    async def discover(self, limit: int) -> list[SongCandidate]:
        """Produce up to `limit` candidates.

        Implementations should return their best candidates first, since the
        caller may take only a prefix.
        """
