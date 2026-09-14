"""Candidates from a CSV or JSON file.

The path for hand-curated lists and for chart exports. Columns beyond title and
artist are optional, and a blank cell means "not known" rather than zero, so a
file carrying only names still produces usable candidates.
"""

import csv
import json
from pathlib import Path

from app.providers.seed.base import CatalogSeedProvider, SongCandidate


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _year(value: str | None) -> int | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return int(text[:4])
    except ValueError:
        return None


class FileSeedProvider(CatalogSeedProvider):
    """Reads candidates from a .csv or .json file."""

    def __init__(self, path: Path, source: str | None = None) -> None:
        self._path = path
        self._source = source or f"file:{path.name}"

    @property
    def name(self) -> str:
        return self._source

    def _rows(self) -> list[dict]:
        if self._path.suffix.lower() == ".json":
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            return payload.get("songs", payload) if isinstance(payload, dict) else payload

        with self._path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    async def discover(self, limit: int) -> list[SongCandidate]:
        candidates: list[SongCandidate] = []

        for row in self._rows():
            title = _clean(row.get("title"))
            artist = _clean(row.get("artist"))
            if not title or not artist:
                continue

            candidates.append(
                SongCandidate(
                    title=title,
                    artist=artist,
                    isrc=_clean(row.get("isrc")),
                    musicbrainz_recording_id=_clean(row.get("musicbrainz_recording_id")),
                    release_year=_year(row.get("release_year")),
                    source=self._source,
                    source_id=_clean(row.get("source_id")),
                )
            )
            if len(candidates) >= limit:
                break

        return candidates
