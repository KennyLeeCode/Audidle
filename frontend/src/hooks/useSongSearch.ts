/**
 * Debounced song search for the guess autocomplete.
 *
 * Owns the query string, the results, and the in-flight request. Two details
 * matter here and both are about not showing the player stale answers:
 *
 * - Requests are debounced, so typing "blinding" fires one request rather than
 *   eight.
 * - Every new request aborts the previous one, so a slow response for "bli"
 *   cannot land after a fast one for "blinding" and overwrite it.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError, searchSongs } from '../services/api'
import type { SongSummary } from '../types/song'

const DEBOUNCE_MS = 180
const MIN_QUERY_LENGTH = 1

export interface UseSongSearchResult {
  query: string
  setQuery: (query: string) => void
  results: SongSummary[]
  isSearching: boolean
  error: string | null
  clear: () => void
}

export function useSongSearch(limit = 8): UseSongSearchResult {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SongSummary[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => {
    const trimmed = query.trim()

    if (trimmed.length < MIN_QUERY_LENGTH) {
      controllerRef.current?.abort()
      setResults([])
      setIsSearching(false)
      setError(null)
      return
    }

    const timer = window.setTimeout(() => {
      controllerRef.current?.abort()
      const controller = new AbortController()
      controllerRef.current = controller

      setIsSearching(true)
      searchSongs(trimmed, limit, controller.signal)
        .then((found) => {
          setResults(found)
          setError(null)
        })
        .catch((thrown: unknown) => {
          // A superseded request is expected, not a failure to report.
          if (thrown instanceof DOMException && thrown.name === 'AbortError') return
          setResults([])
          setError(
            thrown instanceof ApiError ? thrown.message : 'Search is unavailable right now',
          )
        })
        .finally(() => {
          if (!controller.signal.aborted) setIsSearching(false)
        })
    }, DEBOUNCE_MS)

    return () => window.clearTimeout(timer)
  }, [query, limit])

  useEffect(() => () => controllerRef.current?.abort(), [])

  const clear = useCallback(() => {
    controllerRef.current?.abort()
    setQuery('')
    setResults([])
    setError(null)
    setIsSearching(false)
  }, [])

  return { query, setQuery, results, isSearching, error, clear }
}
