/**
 * Song shapes, mirroring the backend schemas in app/schemas/song.py.
 *
 * SongSummary is what search returns and is safe at any time. SongDetail is
 * only ever received from the result endpoint, after a round has ended.
 */

/**
 * A search result.
 *
 * Identified by the provider and that provider's own id, never by Audidle's
 * internal song id. Every song in the catalog has one, so receiving it would
 * tell the player which results could be the answer. The backend resolves the
 * pair back to a song when the guess is submitted.
 */
export interface SongSummary {
  provider: string
  external_id: string
  title: string
  artist: string
  album: string | null
  artwork_url: string | null
}

export interface SongDetail extends SongSummary {
  release_year: number | null
  isrc: string | null
  duration_ms: number | null
  explicit: boolean
  genres: string[]
  external_url: string | null
}

export type PlayableSourceKind = 'file_url' | 'spotify_sdk'

/**
 * How the browser should play this round's audio.
 *
 * `supports_precise_clips` is the field that decides which playback engine the
 * AudioPlayer picks. True means the source is fully decodable, so Web Audio can
 * schedule a clip boundary to the exact sample, which is what makes the 0.01s
 * stage real rather than approximate.
 */
export interface AudioSource {
  kind: PlayableSourceKind
  url: string | null
  spotify_uri: string | null
  duration_ms: number | null
  supports_precise_clips: boolean
}

export interface SearchResponse {
  query: string
  results: SongSummary[]
}
