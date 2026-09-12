/**
 * The guess input and its autocomplete.
 *
 * Owns only interaction state: whether the list is open and which row the
 * keyboard is on. The query and results live in useSongSearch, and submitting a
 * guess is delegated upward.
 *
 * The important rule this component enforces is that a guess is always a chosen
 * song, never free text. Pressing enter without a highlighted result does
 * nothing, because the backend compares track ids and there is nothing sensible
 * to send for a string the player typed.
 */

import { useEffect, useRef, useState } from 'react'

import { useSongSearch } from '../../hooks/useSongSearch'
import { SearchIcon } from '../ui/Icons'
import { SearchResults } from './SearchResults'
import type { SongSummary } from '../../types/song'
import './SongSearch.css'

interface SongSearchProps {
  onGuess: (song: SongSummary) => void
  disabled?: boolean
  /** Triggers the shake animation after a wrong guess. */
  shake?: boolean
}

export function SongSearch({ onGuess, disabled = false, shake = false }: SongSearchProps) {
  const { query, setQuery, results, isSearching, error, clear } = useSongSearch()
  const [isOpen, setIsOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  // Reset the highlight whenever the result set changes, so it never points at
  // a row that has been replaced.
  useEffect(() => setActiveIndex(0), [results])

  const select = (song: SongSummary) => {
    onGuess(song)
    clear()
    setIsOpen(false)
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (!results.length) return

    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex((index) => (index + 1) % results.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((index) => (index - 1 + results.length) % results.length)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const chosen = results[activeIndex]
      if (chosen) select(chosen)
    } else if (event.key === 'Escape') {
      setIsOpen(false)
    }
  }

  return (
    <div className={`song-search ${shake ? 'song-search--shake' : ''}`}>
      <SearchIcon className="song-search__icon" />
      <input
        ref={inputRef}
        className="song-search__input"
        type="text"
        value={query}
        placeholder="Search songs..."
        disabled={disabled}
        autoComplete="off"
        role="combobox"
        aria-expanded={isOpen && query.length > 0}
        aria-autocomplete="list"
        aria-label="Guess the song"
        onChange={(event) => {
          setQuery(event.target.value)
          setIsOpen(true)
        }}
        onFocus={() => setIsOpen(true)}
        onBlur={() => setIsOpen(false)}
        onKeyDown={onKeyDown}
      />

      {isOpen && (
        <SearchResults
          results={results}
          activeIndex={activeIndex}
          isSearching={isSearching}
          error={error}
          hasQuery={query.trim().length > 0}
          onSelect={select}
          onHover={setActiveIndex}
        />
      )}
    </div>
  )
}
