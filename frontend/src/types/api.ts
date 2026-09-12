/** Error shapes returned by the API. */

/** Machine readable codes from app/core/errors.py. */
export type ApiErrorCode =
  | 'round_not_found'
  | 'round_already_ended'
  | 'round_still_active'
  | 'duplicate_guess'
  | 'song_not_found'
  | 'no_eligible_song'
  | 'no_playable_song'
  | 'catalog_unavailable'
  | 'catalog_rate_limited'
  | 'provider_misconfigured'
  | 'internal_error'
  | 'network_error'

export interface ApiErrorBody {
  code: ApiErrorCode
  message: string
}
