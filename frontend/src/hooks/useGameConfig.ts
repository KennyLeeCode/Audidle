/**
 * Fetches the stage ladder and difficulty tiers from the backend.
 *
 * The client renders from this rather than from its own constants, which is
 * what lets you retune CLIP_STAGES server side and have the UI follow without a
 * frontend deploy. The fallbacks in src/config exist only to fill the first
 * paint, and are replaced the moment this resolves.
 */

import { useEffect, useState } from 'react'

import { fetchGameConfig } from '../services/api'
import { FALLBACK_DIFFICULTIES } from '../config/difficulties'
import { FALLBACK_STAGES } from '../config/stages'
import type { GameConfig } from '../types/config'

const FALLBACK_CONFIG: GameConfig = {
  stages: FALLBACK_STAGES,
  total_stages: FALLBACK_STAGES.length,
  difficulties: FALLBACK_DIFFICULTIES,
  auto_next_delay_seconds: 8,
  reroll_counts_as_loss: false,
}

export interface UseGameConfigResult {
  config: GameConfig
  isLoading: boolean
  /** True when the server config could not be fetched and fallbacks are showing. */
  isStale: boolean
}

export function useGameConfig(): UseGameConfigResult {
  const [config, setConfig] = useState<GameConfig>(FALLBACK_CONFIG)
  const [isLoading, setIsLoading] = useState(true)
  const [isStale, setIsStale] = useState(false)

  useEffect(() => {
    const controller = new AbortController()

    fetchGameConfig(controller.signal)
      .then((fetched) => {
        setConfig(fetched)
        setIsStale(false)
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        // The game is still playable on the fallbacks, so this degrades rather
        // than blocking. The flag lets the UI say so.
        setIsStale(true)
      })
      .finally(() => setIsLoading(false))

    return () => controller.abort()
  }, [])

  return { config, isLoading, isStale }
}
