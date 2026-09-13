"""Deciding whether a guess is the right song.

Track id equality is the primary test and it is not sufficient on its own. Real
catalogs carry the same recording under many ids: the album cut, the single, a
remaster, a deluxe reissue, a regional release. A player who picks "Bohemian
Rhapsody - Remastered 2011" when the round drew the 1975 album version has
identified the song correctly, and marking that wrong would be a bug the player
experiences as unfairness.

Three tests, in order of confidence:

  1. Track id. Exact, unambiguous, no false positives.
  2. ISRC. Identifies the *recording*, so it survives rereleases of the same
     master while still separating genuinely different recordings, such as a
     live version or a re-recording.
  3. Normalized title and artist. The fallback for when ISRCs are missing,
     which is common. Deliberately the last resort, because it is the only one
     of the three that can produce a false positive.

Test three is where the care goes. It must treat "Blinding Lights" and
"Blinding Lights - Single Version" as the same song, while still keeping
distinct songs that merely share a title apart, which is why the artist has to
match as well.
"""

import re
import unicodedata

from app.models.song import Song

# Trailing qualifiers that describe a release rather than a song. Anything from
# the first occurrence of one of these onward is dropped.
_RELEASE_QUALIFIERS = re.compile(
    r"""
    \s*[-–—]\s*(
        remaster(ed)?|
        single\ version|
        album\ version|
        radio\ edit|
        mono|stereo|
        \d{4}\ remaster|
        deluxe|
        bonus\ track|
        expanded|
        anniversary\ edition|
        re-?recorded.*|
        taylor's\ version
    ).*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# The same qualifiers when they appear bracketed instead of after a dash.
_BRACKETED_QUALIFIERS = re.compile(
    r"""
    \s*[\(\[][^\)\]]*\b(
        remaster(ed)?|
        single\ version|album\ version|radio\ edit|
        mono|stereo|deluxe|bonus\ track|expanded|
        anniversary|re-?recorded|
        from\ [\"'].*|
        original\ motion\ picture.*
    )\b[^\)\]]*[\)\]]
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Featured artist credits, which appear inconsistently between releases.
_FEATURES = re.compile(
    r"\s*[\(\[]?\s*\b(feat|ft|featuring|with)\b\.?\s[^\)\]]*[\)\]]?",
    re.IGNORECASE,
)

# Apostrophes are removed rather than replaced with a space, so "Don't" becomes
# "dont" and not "don t". Covers the curly variants, which catalogs mix freely
# with the straight one for the same title.
_APOSTROPHES = re.compile(r"['‘’ʼ]")
_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Reduce a title to the song itself, dropping release specific wording.

    "Bohemian Rhapsody - Remastered 2011" and "Bohemian Rhapsody" both become
    "bohemian rhapsody". "Blinding Lights (Single Version)" does too.
    """
    text = unicodedata.normalize("NFKD", title)
    text = _BRACKETED_QUALIFIERS.sub("", text)
    text = _RELEASE_QUALIFIERS.sub("", text)
    text = _FEATURES.sub("", text)
    text = _APOSTROPHES.sub("", text)
    text = _PUNCTUATION.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip().lower()


def normalize_artist(artist: str) -> str:
    """Reduce an artist credit to its primary act.

    Collaborations are credited inconsistently ("A, B" against "A & B" against
    "A feat. B"), so only the first credited artist is compared. That is enough
    to separate two different songs sharing a title, which is all this needs to
    do.
    """
    text = unicodedata.normalize("NFKD", artist)
    text = _FEATURES.sub("", text)
    # Split on any of the separators used to join collaborators.
    primary = re.split(r"\s*[,&;/]\s*|\s+x\s+", text)[0]
    primary = _APOSTROPHES.sub("", primary)
    primary = _PUNCTUATION.sub(" ", primary)
    return _WHITESPACE.sub(" ", primary).strip().lower()


def is_same_song(guess: Song, answer: Song) -> bool:
    """Whether a guessed song is the round's answer.

    Ordered from most to least certain, returning on the first match.
    """
    # 1. The same catalog entry. Nothing to interpret.
    if guess.track_id == answer.track_id:
        return True

    # 2. The same recording under a different catalog entry.
    if guess.isrc and answer.isrc and guess.isrc == answer.isrc:
        return True

    # 3. The same title and artist after stripping release wording. Requires
    #    both, so two unrelated songs called "Home" stay distinct.
    guess_title = normalize_title(guess.title)
    if not guess_title or guess_title != normalize_title(answer.title):
        return False

    return normalize_artist(guess.artist) == normalize_artist(answer.artist)
