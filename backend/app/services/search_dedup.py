"""Collapsing search results into one suggestion per song.

Raw provider search is not a good autocomplete. Searching "tek it" against
Spotify returns the same recording twice under different track ids, plus the
acoustic, sped up, and slowed versions as separate tracks. A player typing a
guess means the song, not a catalogue of its releases.

Two different problems, fixed separately:

**Duplicate releases.** The same recording appears under several provider ids
because it was released as a single and again on an album. These share an ISRC,
which is exactly what ISRCs are for.

**Alternate versions.** The acoustic and sped up cuts are genuinely different
recordings with their own ISRCs, so no identifier collapses them. They are
grouped by canonical title and artist instead, and suppressed only when the
original is also present.

Nothing is deleted. This is presentation: every one of these songs stays in the
catalog, stays eligible, and stays matchable. An alternate version that is the
only thing we hold remains searchable, because suppressing it would make a
guessable song unguessable.
"""

from collections import OrderedDict

from app.models.song import Song
from app.services.song_versions import canonical_key, is_alternate_version


def _identity_groups(songs: list[Song]) -> list[list[Song]]:
    """Group songs that are the same recording, by the strongest key available.

    ISRC first, since it identifies the recording rather than the release. Songs
    with no shared ISRC fall through to their canonical title and artist.
    """
    by_isrc: dict[str, int] = {}
    groups: list[list[Song]] = []

    for song in songs:
        target = None
        for isrc in song.isrcs:
            if isrc in by_isrc:
                target = by_isrc[isrc]
                break

        if target is None:
            target = len(groups)
            groups.append([])

        groups[target].append(song)
        for isrc in song.isrcs:
            by_isrc.setdefault(isrc, target)

    return groups


def dedupe_for_autocomplete(songs: list[Song]) -> list[Song]:
    """Reduce provider results to one suggestion per song.

    Relevance order from the provider is preserved: a song's position is the
    position of the first result that represented it.
    """
    position = {id(song): index for index, song in enumerate(songs)}

    # Grouped by canonical title and artist, so every release and version of one
    # song lands together. The artist is part of the key on purpose: "Stay" by
    # two different acts are unrelated songs and must both survive.
    families: OrderedDict[tuple[str, str], list[Song]] = OrderedDict()
    for song in songs:
        families.setdefault(canonical_key(song.title, song.artist), []).append(song)

    kept: list[Song] = []

    for members in families.values():
        originals = [song for song in members if not is_alternate_version(song.title)]

        if originals:
            # The original is available, so the versions of it are noise here.
            # One suggestion is enough, and duplicate releases of the original
            # collapse on their shared ISRC.
            kept.append(originals[0])
            continue

        # Only alternate versions exist for this song. Suppressing them would
        # make a guessable song unguessable, so they stay, deduplicated against
        # each other by recording identity.
        for group in _identity_groups(members):
            kept.append(group[0])

    return sorted(kept, key=lambda song: position[id(song)])
