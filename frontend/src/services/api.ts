/**
 * Typed HTTP client for the Audidle API.
 *
 * This is the only module in the frontend that knows about fetch, URLs, or
 * status codes. Hooks call these functions and deal in domain types, which
 * keeps request plumbing out of components entirely.
 *
 * Every failure is normalized into an ApiError carrying the backend's own
 * error code, so callers can branch on `round_already_ended` rather than on a
 * status number or a message string.
 */

import type { ApiErrorBody, ApiErrorCode } from '../types/api'
import type { GameConfig } from '../types/config'
import type {
  GuessResponse,
  RoundResult,
  RoundState,
  StartRoundRequest,
} from '../types/game'
import type { SearchResponse, SongSummary } from '../types/song'

/** Dev server proxies /api to the backend, so a relative base works in both. */
const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

export class ApiError extends Error {
  readonly code: ApiErrorCode
  readonly status: number

  constructor(code: ApiErrorCode, message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }

  /** Whether retrying the same call could plausibly succeed. */
  get isRetryable(): boolean {
    return (
      this.code === 'catalog_unavailable' ||
      this.code === 'catalog_rate_limited' ||
      this.code === 'network_error'
    )
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal } = options

  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch (error) {
    // AbortError is a normal control flow signal, not a failure. Rethrowing it
    // unchanged lets callers ignore superseded requests without special casing.
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('network_error', 'Could not reach the server', 0)
  }

  if (!response.ok) {
    throw await toApiError(response)
  }

  return (await response.json()) as T
}

async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as ApiErrorBody
    if (body?.code) {
      return new ApiError(body.code, body.message, response.status)
    }
  } catch {
    // Fall through to the generic case below. A non-JSON error body is still
    // an error, it just carries less information.
  }
  return new ApiError('internal_error', `Request failed (${response.status})`, response.status)
}

// -- Config -----------------------------------------------------------------

export function fetchGameConfig(signal?: AbortSignal): Promise<GameConfig> {
  return request<GameConfig>('/config', { signal })
}

// -- Search -----------------------------------------------------------------

export async function searchSongs(
  query: string,
  limit = 8,
  signal?: AbortSignal,
): Promise<SongSummary[]> {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  const response = await request<SearchResponse>(`/search?${params}`, { signal })
  return response.results
}

// -- Game -------------------------------------------------------------------

export function startRound(payload: StartRoundRequest): Promise<RoundState> {
  return request<RoundState>('/game/round', { method: 'POST', body: payload })
}

export function fetchRound(roundId: string): Promise<RoundState> {
  return request<RoundState>(`/game/${roundId}`)
}

export function submitGuess(roundId: string, trackId: string): Promise<GuessResponse> {
  return request<GuessResponse>(`/game/${roundId}/guess`, {
    method: 'POST',
    body: { track_id: trackId },
  })
}

export function skipStage(roundId: string): Promise<RoundState> {
  return request<RoundState>(`/game/${roundId}/skip`, { method: 'POST' })
}

export function abandonRound(roundId: string): Promise<RoundState> {
  return request<RoundState>(`/game/${roundId}/abandon`, { method: 'POST' })
}

export function fetchResult(roundId: string): Promise<RoundResult> {
  return request<RoundResult>(`/game/${roundId}/result`)
}

/**
 * Resolve a server-relative audio path into something the browser can fetch.
 *
 * The server hands out round scoped paths like /api/game/{id}/audio rather than
 * filenames, specifically so the network tab cannot reveal the answer.
 */
export function resolveAudioUrl(url: string): string {
  if (url.startsWith('http://') || url.startsWith('https://')) return url
  if (url.startsWith('/api')) return url
  return `${API_BASE}${url}`
}
