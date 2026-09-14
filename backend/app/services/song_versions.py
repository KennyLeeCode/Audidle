"""Recognising alternate versions of a recording.

One place for "is this the original, or a version of it". Both the YouTube
matcher and the guess autocomplete need that question answered, and answering it
twice would let the two drift apart.

Two vocabularies, deliberately separate:

`VERSION_TOKENS` are qualifiers that change **which recording** this is. A demo,
a live take, an acoustic rendition, and a remix are different performances with
different audiences, so the YouTube matcher treats a mismatch here as grounds to
reject a candidate outright.

`PRESENTATION_TOKENS` are qualifiers that make a track a poor *autocomplete
suggestion* without necessarily being a different performance. A karaoke or
clean edit is not what a player means when they type a song name. These are used
only when deciding what to show in search, never for identity, so adding one
cannot change how a video is matched or how popularity is measured.
"""

import re
from typing import Final

from app.services.guess_matcher import normalize_artist, normalize_title

# Qualifiers that identify which recording this is.
#
# Matched on word boundaries, since "remix" contains "mix" and a substring check
# would collapse them.
#
# Note what is absent: "remaster". A remaster is the same performance run through
# a different mastering chain, and on YouTube the remastered upload is usually
# *the* official video. Treating it as a distinct version rejected obviously
# correct matches.
VERSION_TOKENS: Final[dict[str, tuple[str, ...]]] = {
    "demo": ("demo",),
    "live": ("live", "concert", "unplugged", "session"),
    "acoustic": ("acoustic",),
    "remix": ("remix", "bootleg", "flip"),
    "radio_edit": ("radio edit",),
    "instrumental": ("instrumental",),
    "edit": ("edit",),
    "mix": ("mix",),
    "single_version": ("single version",),
    "extended": ("extended",),
    "sped_up": ("sped up", "spedup", "slowed"),
}

# Qualifiers that only affect whether a track belongs in autocomplete.
PRESENTATION_TOKENS: Final[dict[str, tuple[str, ...]]] = {
    "nightcore": ("nightcore",),
    "karaoke": ("karaoke", "backing track"),
    "clean": ("clean",),
    "reverb": ("reverb", "8d audio"),
}

ALL_TOKENS: Final[dict[str, tuple[str, ...]]] = {**VERSION_TOKENS, **PRESENTATION_TOKENS}


def _compile(tokens: dict[str, tuple[str, ...]]) -> dict[str, re.Pattern[str]]:
    return {
        name: re.compile(r"\b(" + "|".join(re.escape(word) for word in words) + r")\b", re.I)
        for name, words in tokens.items()
    }


_VERSION_PATTERNS = _compile(VERSION_TOKENS)
_ALL_PATTERNS = _compile(ALL_TOKENS)


def _found(title: str, patterns: dict[str, re.Pattern[str]]) -> set[str]:
    found = {name for name, pattern in patterns.items() if pattern.search(title)}

    # Longer qualifiers absorb the shorter ones they contain, so a radio edit is
    # not separately reported as an edit and a remix is not also a mix.
    if "remix" in found:
        found.discard("mix")
    if "radio_edit" in found:
        found.discard("edit")
    if "sped_up" in found:
        found.discard("edit")
    return found


def extract_versions(title: str) -> frozenset[str]:
    """Which identity-affecting version qualifiers a title claims.

    Used by the YouTube matcher to compare a catalog title against a video
    title. Presentation-only tokens are deliberately excluded, so what counts as
    the same recording never shifts because of an autocomplete change.
    """
    return frozenset(_found(title, _VERSION_PATTERNS))


def strip_version_words(title: str) -> str:
    """Remove identity-affecting version wording from a title."""
    text = title
    for pattern in _VERSION_PATTERNS.values():
        text = pattern.sub(" ", text)
    return text


def is_alternate_version(title: str) -> bool:
    """Whether a title is a version of something rather than the thing itself.

    Broader than `extract_versions`, because this decides what to show a player
    typing a guess. "Tek It - Sped Up" and "Song (Karaoke)" are both poor
    suggestions when the original is available, for different reasons.
    """
    return bool(_found(title, _ALL_PATTERNS))


_BRACKETED = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_TRAILING_SEGMENT = re.compile(r"\s[-–—]\s[^-–—]*$")


def _mentions_version(text: str) -> bool:
    return any(pattern.search(text) for pattern in _ALL_PATTERNS.values())


def canonical_title(title: str) -> str:
    """The title with every version qualifier removed, normalized.

    Whole segments are dropped rather than single words. "Paper Lantern (Kygo
    Remix)" must reduce to "paper lantern", not "paper lantern kygo": the
    remixer's name rides along with the qualifier, and leaving it behind put the
    remix in a different family from the song it remixes, which defeated the
    grouping entirely.
    """
    text = _BRACKETED.sub(
        lambda match: " " if _mentions_version(match.group()) else match.group(), title
    )

    # A trailing "- Sped Up" or "- Live at Wembley" carries the same risk.
    while True:
        trailing = _TRAILING_SEGMENT.search(text)
        if not trailing or not _mentions_version(trailing.group()):
            break
        text = text[: trailing.start()]

    for pattern in _ALL_PATTERNS.values():
        text = pattern.sub(" ", text)
    return normalize_title(text)


def canonical_key(title: str, artist: str) -> tuple[str, str]:
    """Identity for grouping releases of the same song by the same artist.

    Includes the artist on purpose. Grouping on title alone would merge "Stay"
    by one act with "Stay" by another, which are unrelated songs that must both
    remain guessable.
    """
    return (canonical_title(title), normalize_artist(artist))
