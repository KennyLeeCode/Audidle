/**
 * Game shapes, mirroring the backend schemas in app/schemas/game.py.
 *
 * Note what RoundState does not contain: any field identifying the song. That
 * is not an omission in this file, it is the actual shape of what the server
 * sends while a round is in progress.
 */

import type { AudioSource, SongDetail } from './song'

export type Difficulty = 'easy' | 'medium' | 'hard' | 'expert' | 'impossible'

/** Lifecycle the server persists. Only ever one of these two. */
export type RoundStatus = 'playing' | 'revealing'

/**
 * The client's view of the lifecycle.
 *
 * `won` and `failed` are brief flash states that only exist on the frontend.
 * They render the success or failure animation and then auto-advance into
 * `revealing`, which is the screen that shows the answer and plays the full
 * track. The server never persists them.
 */
export type GameStatus = 'loading' | 'playing' | 'won' | 'failed' | 'revealing' | 'error'

export type RoundOutcome = 'won' | 'failed' | 'abandoned'

export type SongStartMode = 'from_start' | 'main_hook'

export interface RoundState {
  round_id: string
  difficulty: Difficulty
  stage_index: number
  /** Seconds of audio currently unlocked. Sent by the server, never computed here. */
  clip_duration: number
  total_stages: number
  is_final_stage: boolean
  status: RoundStatus
  guess_count: number
  /** Offset every clip in this round starts from. Always 0 for from_start. */
  start_offset_ms: number
  audio: AudioSource | null
}

export interface GuessResponse {
  correct: boolean
  round_ended: boolean
  round: RoundState
  /** Echo of what the player picked, so wrong attempts can be listed. Never the answer. */
  guessed_track: SongDetail | null
}

export interface RoundResult {
  round_id: string
  difficulty: Difficulty
  outcome: RoundOutcome
  won: boolean
  stages_used: number
  final_stage_index: number
  duration_reached: number
  total_stages: number
  guess_count: number
  song: SongDetail
  audio: AudioSource | null
}

/** A guess the player made this round, kept client side for the attempts list. */
export interface LocalGuess {
  track_id: string
  title: string
  artist: string
  artwork_url: string | null
  stage_index: number
}

export interface StartRoundRequest {
  difficulty: Difficulty
  session_id?: string | null
  start_mode?: SongStartMode
  genres?: string[] | null
  decades?: number[] | null
  explicit?: boolean | null
}
