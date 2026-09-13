/**
 * Round lifecycle orchestration.
 *
 * This hook owns the client's view of a round and coordinates the two things
 * that have to stay in step: the server's round state, and what the audio
 * player is doing. It deliberately owns no rules. Whether a guess was right,
 * whether a stage advances, and whether the round is over are all decided by
 * the backend and simply reflected here.
 *
 * The one piece of state that is genuinely client side is the brief `won` and
 * `failed` flash. The server goes straight from `playing` to `revealing`, and
 * this hook holds the intermediate state just long enough to play the success
 * or failure animation before the reveal takes over.
 *
 * What could be extended later: filters are already threaded through
 * startRound, so wiring the Filters panel to them needs no change here.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import * as api from '../services/api'
import { ApiError } from '../services/api'
import type { AudioPlayer } from '../audio/AudioPlayer'
import type { GameConfig } from '../types/config'
import type {
  Difficulty,
  GameStatus,
  LocalGuess,
  RoundResult,
  RoundState,
} from '../types/game'
import type { SongSummary } from '../types/song'

/** How long the win or loss animation holds before the reveal replaces it. */
const FLASH_MS = 1100

const SESSION_KEY = 'audidle:session'

/** Stable anonymous id, so the server can avoid repeating recent songs. */
function loadSessionId(): string {
  try {
    const existing = window.localStorage.getItem(SESSION_KEY)
    if (existing) return existing
    const created = crypto.randomUUID()
    window.localStorage.setItem(SESSION_KEY, created)
    return created
  } catch {
    // Storage blocked. A per-tab id still gets recent-song avoidance within
    // this session, which is the majority of the benefit.
    return crypto.randomUUID()
  }
}

export interface UseGameResult {
  status: GameStatus
  round: RoundState | null
  result: RoundResult | null
  difficulty: Difficulty
  guesses: LocalGuess[]
  error: string | null
  /** Set briefly after a wrong guess, so the input can shake. */
  lastGuessWrong: boolean
  isBusy: boolean
  autoNext: boolean
  setAutoNext: (enabled: boolean) => void
  selectDifficulty: (difficulty: Difficulty) => void
  playCurrentClip: () => void
  submitGuess: (song: SongSummary) => Promise<void>
  skip: () => Promise<void>
  reroll: () => Promise<void>
  nextSong: () => Promise<void>
  retry: () => void
}

export function useGame(player: AudioPlayer, config: GameConfig): UseGameResult {
  const [status, setStatus] = useState<GameStatus>('loading')
  const [round, setRound] = useState<RoundState | null>(null)
  const [result, setResult] = useState<RoundResult | null>(null)
  const [difficulty, setDifficulty] = useState<Difficulty>('easy')
  const [guesses, setGuesses] = useState<LocalGuess[]>([])
  const [error, setError] = useState<string | null>(null)
  const [lastGuessWrong, setLastGuessWrong] = useState(false)
  const [isBusy, setIsBusy] = useState(false)
  const [autoNext, setAutoNext] = useState(false)

  const sessionIdRef = useRef<string>(loadSessionId())
  // Incremented on every round change, so a timer or request belonging to an
  // abandoned round cannot act on the current one.
  const roundTokenRef = useRef(0)
  const flashTimerRef = useRef<number | null>(null)
  const autoNextTimerRef = useRef<number | null>(null)

  const clearTimers = useCallback(() => {
    if (flashTimerRef.current !== null) window.clearTimeout(flashTimerRef.current)
    if (autoNextTimerRef.current !== null) window.clearTimeout(autoNextTimerRef.current)
    flashTimerRef.current = null
    autoNextTimerRef.current = null
  }, [])

  // -- Round creation -------------------------------------------------------

  const beginRound = useCallback(
    async (nextDifficulty: Difficulty) => {
      const token = ++roundTokenRef.current
      clearTimers()
      player.reset()

      setStatus('loading')
      setError(null)
      setResult(null)
      setGuesses([])
      setLastGuessWrong(false)

      try {
        const started = await api.startRound({
          difficulty: nextDifficulty,
          session_id: sessionIdRef.current,
        })
        if (token !== roundTokenRef.current) return

        setRound(started)
        setStatus('playing')

        // Decode up front so the first press is instant. A stuttering first
        // clip at 0.01s would be indistinguishable from a bug.
        if (started.audio?.url) {
          await player.prepare(started.audio, api.resolveAudioUrl(started.audio.url))
        }
      } catch (thrown) {
        if (token !== roundTokenRef.current) return
        setStatus('error')
        setError(
          thrown instanceof ApiError
            ? thrown.message
            : 'Could not start a round. Is the backend running?',
        )
      }
    },
    [clearTimers, player],
  )

  // Open the first round once, on mount.
  useEffect(() => {
    void beginRound('easy')
    return clearTimers
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // -- Ending a round -------------------------------------------------------

  /**
   * Handle the transition into the reveal.
   *
   * Order matters: the flash renders first, the result is fetched during it,
   * and full playback only starts once the reveal is actually on screen.
   */
  const endRound = useCallback(
    async (roundId: string, won: boolean) => {
      const token = roundTokenRef.current
      setStatus(won ? 'won' : 'failed')

      let fetched: RoundResult | null = null
      try {
        fetched = await api.fetchResult(roundId)
      } catch (thrown) {
        if (token !== roundTokenRef.current) return
        setError(thrown instanceof ApiError ? thrown.message : 'Could not load the result')
      }
      if (token !== roundTokenRef.current) return
      setResult(fetched)

      flashTimerRef.current = window.setTimeout(() => {
        if (token !== roundTokenRef.current) return
        setStatus('revealing')

        // Full playback, not clip mode. The player is free to listen to the
        // whole track while the reveal sits on screen.
        void player.playFullTrack(0)

        if (autoNext) {
          autoNextTimerRef.current = window.setTimeout(
            () => {
              if (token === roundTokenRef.current) void beginRound(difficulty)
            },
            config.auto_next_delay_seconds * 1000,
          )
        }
      }, FLASH_MS)
    },
    [autoNext, beginRound, config.auto_next_delay_seconds, difficulty, player],
  )

  // -- Player actions -------------------------------------------------------

  /**
   * Replay the currently unlocked prefix.
   *
   * Always from the round's start offset, never from where the last clip
   * stopped, so pressing play twice gives the identical clip twice.
   */
  const playCurrentClip = useCallback(() => {
    if (!round || status !== 'playing') return
    const stage = config.stages[round.stage_index]
    void player.playClip(
      round.clip_duration,
      round.start_offset_ms,
      stage?.fade_ms ?? 8,
    )
  }, [config.stages, player, round, status])

  const submitGuess = useCallback(
    async (song: SongSummary) => {
      if (!round || status !== 'playing' || isBusy) return
      setIsBusy(true)
      const token = roundTokenRef.current

      try {
        const response = await api.submitGuess(round.round_id, song.provider, song.external_id)
        if (token !== roundTokenRef.current) return

        setRound(response.round)
        setGuesses((previous) => [
          ...previous,
          {
            provider: song.provider,
            external_id: song.external_id,
            title: song.title,
            artist: song.artist,
            artwork_url: song.artwork_url,
            stage_index: round.stage_index,
          },
        ])

        if (response.correct) {
          player.stop()
          await endRound(round.round_id, true)
        } else if (response.round_ended) {
          player.stop()
          await endRound(round.round_id, false)
        } else {
          // Wrong, stages remain. The song is unchanged and the next clip
          // length is now unlocked. Deliberately not auto-played: the player
          // decides when to hear it.
          setLastGuessWrong(true)
          window.setTimeout(() => setLastGuessWrong(false), 500)
        }
      } catch (thrown) {
        if (token !== roundTokenRef.current) return
        setError(thrown instanceof ApiError ? thrown.message : 'Your guess could not be sent')
      } finally {
        setIsBusy(false)
      }
    },
    [endRound, isBusy, player, round, status],
  )

  /** Surrender this attempt and unlock more of the same song. */
  const skip = useCallback(async () => {
    if (!round || status !== 'playing' || isBusy) return
    setIsBusy(true)
    const token = roundTokenRef.current

    try {
      const updated = await api.skipStage(round.round_id)
      if (token !== roundTokenRef.current) return
      setRound(updated)

      if (updated.status === 'revealing') {
        player.stop()
        await endRound(round.round_id, false)
      }
    } catch (thrown) {
      if (token !== roundTokenRef.current) return
      setError(thrown instanceof ApiError ? thrown.message : 'Skip failed')
    } finally {
      setIsBusy(false)
    }
  }, [endRound, isBusy, player, round, status])

  /**
   * Abandon the whole round and draw a different song.
   *
   * Distinct from skip, which keeps the song. The backend decides whether this
   * counts as a loss, via REROLL_COUNTS_AS_LOSS.
   */
  const reroll = useCallback(async () => {
    if (isBusy) return
    if (round && status === 'playing') {
      try {
        await api.abandonRound(round.round_id)
      } catch {
        // Losing the abandon call is not worth blocking a reroll over. The
        // round expires on its own.
      }
    }
    await beginRound(difficulty)
  }, [beginRound, difficulty, isBusy, round, status])

  /** Leave the reveal and start a fresh round on the same difficulty. */
  const nextSong = useCallback(async () => {
    player.stop()
    await beginRound(difficulty)
  }, [beginRound, difficulty, player])

  /**
   * Change difficulty.
   *
   * Starts a new round, since the current song belongs to the old tier. The
   * chosen difficulty then persists across Next Song until changed again.
   */
  const selectDifficulty = useCallback(
    (next: Difficulty) => {
      if (next === difficulty) return
      setDifficulty(next)
      if (round && status === 'playing') void api.abandonRound(round.round_id).catch(() => {})
      void beginRound(next)
    },
    [beginRound, difficulty, round, status],
  )

  const retry = useCallback(() => {
    void beginRound(difficulty)
  }, [beginRound, difficulty])

  return {
    status,
    round,
    result,
    difficulty,
    guesses,
    error,
    lastGuessWrong,
    isBusy,
    autoNext,
    setAutoNext,
    selectDifficulty,
    playCurrentClip,
    submitGuess,
    skip,
    reroll,
    nextSong,
    retry,
  }
}
