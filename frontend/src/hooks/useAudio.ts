/**
 * Binds the AudioPlayer singleton to React.
 *
 * The player itself is a plain class with a subscription, so this hook is thin
 * on purpose: it holds one instance for the life of the component tree, mirrors
 * its state into React, and tears it down on unmount. No audio logic lives here.
 *
 * Volume is persisted to localStorage, since a player who turns it down should
 * not have to do it again on every visit.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { AudioPlayer } from '../audio/AudioPlayer'
import type { PlaybackState } from '../audio/types'

const VOLUME_KEY = 'audidle:volume'

function loadStoredVolume(): number {
  try {
    const stored = window.localStorage.getItem(VOLUME_KEY)
    const parsed = stored === null ? NaN : Number.parseFloat(stored)
    return Number.isFinite(parsed) ? Math.min(1, Math.max(0, parsed)) : 1
  } catch {
    // Private mode or blocked storage. A default volume is not worth failing over.
    return 1
  }
}

export interface UseAudioResult {
  player: AudioPlayer
  playback: PlaybackState
  volume: number
  setVolume: (volume: number) => void
}

export function useAudio(): UseAudioResult {
  const playerRef = useRef<AudioPlayer | null>(null)
  if (playerRef.current === null) {
    playerRef.current = new AudioPlayer()
  }
  const player = playerRef.current

  const [volume, setVolumeState] = useState(loadStoredVolume)
  const [playback, setPlayback] = useState<PlaybackState>(() => player.snapshot())

  useEffect(() => player.subscribe(setPlayback), [player])

  // Apply the stored volume once on mount, and again whenever it changes.
  useEffect(() => {
    player.setVolume(volume)
  }, [player, volume])

  useEffect(() => () => player.dispose(), [player])

  const setVolume = useCallback((next: number) => {
    const clamped = Math.min(1, Math.max(0, next))
    setVolumeState(clamped)
    try {
      window.localStorage.setItem(VOLUME_KEY, String(clamped))
    } catch {
      // Not worth surfacing. The volume still applies for this session.
    }
  }, [])

  return useMemo(
    () => ({ player, playback, volume, setVolume }),
    [player, playback, volume, setVolume],
  )
}
