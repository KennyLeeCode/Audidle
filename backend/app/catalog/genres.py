"""Genre name normalization.

Genre strings arrive spelled inconsistently from every source. MusicBrainz tags
are free text and lowercase, seed data is hand written, and different providers
punctuate the same genre differently. Slugging collapses those into one row.

The line this draws: fold spelling and punctuation differences, do not fold
genuinely different genres. "Hip Hop", "hip-hop", and "hip hop" are one thing.
"house" and "deep house" are two, and merging them would quietly destroy
information that the genre filter depends on.
"""

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Spellings that mean the same genre. Only entries where the difference is
# purely orthographic or a well established synonym, never a parent genre
# swallowing a subgenre.
_ALIASES: dict[str, str] = {
    "hiphop": "hip-hop",
    "hip-hop-rap": "hip-hop",
    "rap": "hip-hop",
    "rnb": "r-and-b",
    "r-b": "r-and-b",
    "randb": "r-and-b",
    "rhythm-and-blues": "r-and-b",
    "electronica": "electronic",
    "edm": "electronic-dance-music",
    "alt-rock": "alternative-rock",
    "alt": "alternative",
    "indie-pop-rock": "indie",
    "synth-pop": "synthpop",
    "dreampop": "dream-pop",
    "singer-songwriter": "singer-songwriter",
    "drum-n-bass": "drum-and-bass",
    "dnb": "drum-and-bass",
    "psychedelia": "psychedelic",
    "80s": "1980s",
    "90s": "1990s",
    "70s": "1970s",
    "60s": "1960s",
}


def slugify_genre(name: str) -> str:
    """Reduce a genre name to its canonical slug.

    Returns an empty string for input that has no letters or digits, which the
    caller should treat as "no genre" rather than storing.
    """
    folded = unicodedata.normalize("NFKD", name.strip().lower())
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    # Expanded rather than stripped, so "Drum & Bass" and "Drum and Bass" agree
    # instead of becoming "drum-bass" and "drum-and-bass".
    folded = folded.replace("&", " and ").replace("+", " and ")
    slug = _NON_ALNUM.sub("-", folded).strip("-")
    return _ALIASES.get(slug, slug)


def display_name(slug: str) -> str:
    """Turn a slug back into something readable for the UI."""
    if not slug:
        return ""
    words = slug.split("-")
    # Acronyms that look wrong title cased.
    special = {"r": "R", "and": "and", "b": "B", "edm": "EDM", "dj": "DJ"}
    return " ".join(special.get(word, word.capitalize()) for word in words)
