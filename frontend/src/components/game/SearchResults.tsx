/**
 * The autocomplete dropdown.
 *
 * Presentational only. It has no idea which song is correct, and that is by
 * design: if it knew, it would be tempting to mark or reorder the answer, and
 * either would identify it visually.
 */

import type { SongSummary } from '../../types/song'
import './SearchResults.css'

interface SearchResultsProps {
  results: SongSummary[]
  /** Index moved by arrow keys, owned by the parent so typing can drive it. */
  activeIndex: number
  isSearching: boolean
  error: string | null
  hasQuery: boolean
  onSelect: (song: SongSummary) => void
  onHover: (index: number) => void
}

export function SearchResults({
  results,
  activeIndex,
  isSearching,
  error,
  hasQuery,
  onSelect,
  onHover,
}: SearchResultsProps) {
  if (!hasQuery) return null

  if (error) {
    return (
      <div className="search-results search-results--message" role="status">
        {error}
      </div>
    )
  }

  if (!results.length) {
    return (
      <div className="search-results search-results--message" role="status">
        {isSearching ? 'Searching...' : 'No songs match that'}
      </div>
    )
  }

  return (
    <ul className="search-results" role="listbox" aria-label="Song suggestions">
      {results.map((song, index) => (
        <li key={song.track_id}>
          <button
            type="button"
            role="option"
            aria-selected={index === activeIndex}
            className={`search-result ${index === activeIndex ? 'search-result--active' : ''}`}
            /* Mouse down rather than click, so selecting a result fires before
               the input's blur handler closes the list. */
            onMouseDown={(event) => {
              event.preventDefault()
              onSelect(song)
            }}
            onMouseEnter={() => onHover(index)}
          >
            <span className="search-result__art">
              {song.artwork_url ? (
                <img src={song.artwork_url} alt="" loading="lazy" />
              ) : (
                /* Artwork is frequently missing, so the fallback is a real
                   state rather than a broken image. */
                <span className="search-result__art-fallback" aria-hidden="true">
                  {song.title.charAt(0).toUpperCase()}
                </span>
              )}
            </span>
            <span className="search-result__text">
              <span className="search-result__title">{song.title}</span>
              <span className="search-result__artist">{song.artist}</span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}
