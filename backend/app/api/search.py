"""Song search routes, backing the guess autocomplete.

Note what this endpoint does not do: it has no idea which round is in progress
and no access to any answer. That is intentional. If search knew the answer it
would be tempting to mark or exclude it, and either would identify the correct
song visually, which is exactly what the rules forbid.
"""

from fastapi import APIRouter, Query

from app.dependencies import SongServiceDep
from app.schemas.song import SearchResponse, SongSummary

router = APIRouter(tags=["search"])

MAX_RESULTS = 25


@router.get("/search", response_model=SearchResponse, summary="Search the song catalog")
async def search_songs(
    songs: SongServiceDep,
    q: str = Query(min_length=1, max_length=120, description="Free text query"),
    limit: int = Query(default=10, ge=1, le=MAX_RESULTS),
) -> SearchResponse:
    """Return catalog matches for the autocomplete dropdown.

    Results carry a track id, which is what the client submits as a guess.
    """
    results = await songs.search(q, limit=limit)
    provider = songs.provider_name
    return SearchResponse(
        query=q,
        results=[SongSummary.from_domain(song, provider) for song in results],
    )
