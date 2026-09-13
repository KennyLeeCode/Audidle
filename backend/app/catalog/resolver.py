"""Identity resolution for catalog ingestion.

Answers one question: does this candidate from a provider refer to a recording
we already have, and how sure are we?

This is a different job from guess_matcher, which decides whether a *player's
guess* is the round's answer. That one is allowed to be generous, because
accepting a remaster the player correctly identified is the right outcome. This
one must be conservative, because a wrong merge corrupts the catalog and is far
harder to undo than an unmatched song is to revisit.

Why conservative matters, from real data. Searching MusicBrainz for "Blinding
Lights" by The Weeknd returns several recordings scored 100, and the top one
carries ISRC USUMV2403154 while Spotify's carries USUG11904206. Those are
genuinely different recordings. Accepting the top scored title match would have
attached the wrong MusicBrainz identity to our song, silently.

The confidence ladder, strongest first:

  EXACT_PROVIDER_ID   the same provider id we already store. Certain.
  EXACT_ISRC          a shared ISRC. Identifies the recording, not the release.
  EXACT_MBID          the same MusicBrainz recording id.
  HIGH_CONFIDENCE     artist and title agree and durations are close.
  UNRESOLVED          anything weaker, including title-only agreement.

Only the first four are accepted. UNRESOLVED goes to the review queue.
"""

import logging
from dataclasses import dataclass
from enum import Enum

from app.services.guess_matcher import normalize_artist, normalize_title

logger = logging.getLogger(__name__)

# How far two durations may differ and still be considered the same recording.
# Three seconds covers differing trailing silence and metadata rounding between
# providers, and is tight enough to separate a radio edit from an album cut.
DURATION_TOLERANCE_MS = 3_000


class MatchConfidence(str, Enum):
    """How a match was established. Ordered strongest to weakest."""

    EXACT_PROVIDER_ID = "exact_provider_id"
    EXACT_ISRC = "exact_isrc"
    EXACT_MBID = "exact_mbid"
    HIGH_CONFIDENCE_METADATA = "high_confidence_metadata"
    MANUAL = "manual"
    UNRESOLVED = "unresolved"

    @property
    def is_acceptable(self) -> bool:
        """Whether this match may be written into the catalog automatically."""
        return self is not MatchConfidence.UNRESOLVED


@dataclass(frozen=True)
class Candidate:
    """A provider's version of a recording, normalized for comparison."""

    provider: str
    external_id: str
    title: str
    artist: str
    isrcs: tuple[str, ...] = ()
    duration_ms: int | None = None
    musicbrainz_id: str | None = None


@dataclass(frozen=True)
class MatchResult:
    """The outcome of comparing a candidate to an existing song."""

    confidence: MatchConfidence
    reason: str

    @property
    def accepted(self) -> bool:
        return self.confidence.is_acceptable


@dataclass(frozen=True)
class ExistingSong:
    """What the resolver needs to know about a song already in the catalog."""

    song_id: str
    title: str
    artist: str
    isrcs: tuple[str, ...] = ()
    duration_ms: int | None = None
    musicbrainz_id: str | None = None
    provider_ids: dict[str, str] | None = None


def durations_agree(left: int | None, right: int | None) -> bool:
    """Whether two durations are close enough to be the same recording.

    Missing durations count as agreement rather than disagreement, because
    plenty of catalog entries have none and treating that as a mismatch would
    reject correct matches. The title and artist checks still have to pass.
    """
    if left is None or right is None:
        return True
    return abs(left - right) <= DURATION_TOLERANCE_MS


def resolve_match(candidate: Candidate, existing: ExistingSong) -> MatchResult:
    """Decide whether a candidate is the same recording as an existing song."""
    # 1. The same provider id we already store. Nothing to interpret.
    stored = (existing.provider_ids or {}).get(candidate.provider)
    if stored and stored == candidate.external_id:
        return MatchResult(
            MatchConfidence.EXACT_PROVIDER_ID,
            f"{candidate.provider} id {candidate.external_id} already attached",
        )

    # 2. A shared ISRC. The strongest cross-provider evidence there is, because
    #    an ISRC identifies the recording rather than the release.
    shared = set(candidate.isrcs) & set(existing.isrcs)
    if shared:
        return MatchResult(
            MatchConfidence.EXACT_ISRC, f"shared ISRC {sorted(shared)[0]}"
        )

    # 3. The same MusicBrainz recording.
    if (
        candidate.musicbrainz_id
        and existing.musicbrainz_id
        and candidate.musicbrainz_id == existing.musicbrainz_id
    ):
        return MatchResult(
            MatchConfidence.EXACT_MBID, f"shared MBID {candidate.musicbrainz_id}"
        )

    # 3a. Both sides have ISRCs and none are shared. That is positive evidence
    #     of a *different* recording, not merely absent evidence, so metadata
    #     agreement is not allowed to override it. This is the check that would
    #     have caught the two different "Blinding Lights" recordings.
    if candidate.isrcs and existing.isrcs:
        return MatchResult(
            MatchConfidence.UNRESOLVED,
            f"both have ISRCs and none match "
            f"({sorted(candidate.isrcs)[0]} against {sorted(existing.isrcs)[0]})",
        )

    # 4. Metadata agreement, only when the artist, the title, and the duration
    #    all line up. Title alone is never enough.
    candidate_title = normalize_title(candidate.title)
    existing_title = normalize_title(existing.title)
    titles_agree = bool(candidate_title) and candidate_title == existing_title
    artists_agree = normalize_artist(candidate.artist) == normalize_artist(existing.artist)

    if titles_agree and artists_agree:
        if durations_agree(candidate.duration_ms, existing.duration_ms):
            return MatchResult(
                MatchConfidence.HIGH_CONFIDENCE_METADATA,
                "artist, title, and duration agree",
            )
        return MatchResult(
            MatchConfidence.UNRESOLVED,
            f"artist and title agree but durations differ by "
            f"{abs((candidate.duration_ms or 0) - (existing.duration_ms or 0))}ms",
        )

    if titles_agree and not artists_agree:
        # A cover or a different act with the same song name.
        return MatchResult(
            MatchConfidence.UNRESOLVED,
            f"title matches but artist differs ({candidate.artist} against {existing.artist})",
        )

    return MatchResult(MatchConfidence.UNRESOLVED, "no matching identifier or metadata")


def best_match(
    candidate: Candidate, existing_songs: list[ExistingSong]
) -> tuple[ExistingSong | None, MatchResult]:
    """Pick the strongest match among several existing songs.

    Returns the winner and its result, or None plus the best explanation when
    nothing was confident enough. Ambiguity is reported rather than broken by a
    tiebreak: two songs matching at the same strength means the catalog already
    holds a duplicate, and picking one arbitrarily would compound it.
    """
    ranking = {
        MatchConfidence.EXACT_PROVIDER_ID: 5,
        MatchConfidence.EXACT_ISRC: 4,
        MatchConfidence.EXACT_MBID: 3,
        MatchConfidence.HIGH_CONFIDENCE_METADATA: 2,
        MatchConfidence.MANUAL: 1,
        MatchConfidence.UNRESOLVED: 0,
    }

    scored = [(resolve_match(candidate, song), song) for song in existing_songs]
    accepted = [(result, song) for result, song in scored if result.accepted]

    if not accepted:
        if scored:
            # Surface the most informative explanation rather than a generic one.
            return None, max(scored, key=lambda pair: len(pair[0].reason))[0]
        return None, MatchResult(MatchConfidence.UNRESOLVED, "no existing songs to compare")

    accepted.sort(key=lambda pair: -ranking[pair[0].confidence])
    best_result, best_song = accepted[0]

    tied = [
        song
        for result, song in accepted[1:]
        if ranking[result.confidence] == ranking[best_result.confidence]
    ]
    if tied:
        return None, MatchResult(
            MatchConfidence.UNRESOLVED,
            f"ambiguous, {len(tied) + 1} songs match at {best_result.confidence.value}",
        )

    return best_song, best_result
