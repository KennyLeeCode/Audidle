"""Difficulty tiers and their popularity thresholds.

Difficulty is defined once here and nowhere else. Two thresholds are declared
per tier because the game supports two kinds of popularity source:

  stream_floor / stream_ceiling
      Real Spotify stream counts, supplied by a curated dataset. Preferred.

  popularity_floor / popularity_ceiling
      Spotify's own 0-100 popularity score, used as a fallback when no stream
      data is available. This is an approximation, not a stream count. Spotify
      does not expose stream counts through its Web API at all.

Whichever PopularityProvider is wired up reads the field it can actually honour.
"""

from dataclasses import dataclass
from typing import Final

from app.models.enums import Difficulty


@dataclass(frozen=True)
class DifficultyConfig:
    """Static configuration for one difficulty tier."""

    key: Difficulty
    label: str
    # Accent colour used by the frontend for this tier.
    color: str
    # Inclusive lower bound on estimated Spotify streams.
    stream_floor: int
    # Exclusive upper bound, or None for "no ceiling".
    stream_ceiling: int | None
    # Fallback band over Spotify's 0-100 popularity score.
    popularity_floor: int
    popularity_ceiling: int
    description: str

    def matches_streams(self, streams: int) -> bool:
        """Whether a stream count falls inside this tier."""
        if streams < self.stream_floor:
            return False
        return self.stream_ceiling is None or streams < self.stream_ceiling

    def matches_popularity(self, popularity: int) -> bool:
        """Whether a Spotify popularity score falls inside this tier."""
        return self.popularity_floor <= popularity <= self.popularity_ceiling


DIFFICULTIES: Final[dict[Difficulty, DifficultyConfig]] = {
    Difficulty.EASY: DifficultyConfig(
        key=Difficulty.EASY,
        label="Easy",
        color="#1ed760",
        stream_floor=1_000_000_000,
        stream_ceiling=None,
        popularity_floor=82,
        popularity_ceiling=100,
        description="Household names. Roughly 1 billion streams and up.",
    ),
    Difficulty.MEDIUM: DifficultyConfig(
        key=Difficulty.MEDIUM,
        label="Medium",
        color="#f5c344",
        stream_floor=500_000_000,
        stream_ceiling=1_000_000_000,
        popularity_floor=70,
        popularity_ceiling=81,
        description="Widely known hits. Roughly 500 million to 1 billion streams.",
    ),
    Difficulty.HARD: DifficultyConfig(
        key=Difficulty.HARD,
        label="Hard",
        color="#f08a3c",
        stream_floor=100_000_000,
        stream_ceiling=500_000_000,
        popularity_floor=55,
        popularity_ceiling=69,
        description="Familiar if you follow the genre. 100 to 500 million streams.",
    ),
    Difficulty.EXPERT: DifficultyConfig(
        key=Difficulty.EXPERT,
        label="Expert",
        color="#e5484d",
        stream_floor=25_000_000,
        stream_ceiling=100_000_000,
        popularity_floor=35,
        popularity_ceiling=54,
        description="Deep cuts and smaller artists. 25 to 100 million streams.",
    ),
    Difficulty.IMPOSSIBLE: DifficultyConfig(
        key=Difficulty.IMPOSSIBLE,
        label="Impossible",
        color="#a06bd8",
        stream_floor=0,
        stream_ceiling=25_000_000,
        popularity_floor=0,
        popularity_ceiling=34,
        description="Obscure. Under roughly 25 million streams.",
    ),
}


def get_difficulty_config(difficulty: Difficulty) -> DifficultyConfig:
    """Look up a tier's configuration."""
    return DIFFICULTIES[difficulty]


def difficulty_for_streams(streams: int) -> Difficulty:
    """Classify a stream count into a difficulty tier.

    Used when ingesting a catalog so each song can be bucketed once instead of
    being re-classified on every round.
    """
    for config in DIFFICULTIES.values():
        if config.matches_streams(streams):
            return config.key
    return Difficulty.IMPOSSIBLE
