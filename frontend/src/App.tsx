/**
 * Application shell.
 *
 * Composes the three columns and routes between the play, flash, and reveal
 * screens. It holds almost no state of its own: useGame owns the round, useAudio
 * owns playback, and this decides what to render from their combination.
 *
 * The one thing it does own is the accent. Setting --accent from the selected
 * difficulty here is what retints the entire center column, without a single
 * conditional class name anywhere below.
 */

import { useEffect, useMemo, useState } from 'react'

import { GamePlayer } from './components/game/GamePlayer'
import { ResultFlash } from './components/result/ResultFlash'
import { ResultView } from './components/result/ResultView'
import { SettingsPanel } from './components/layout/SettingsPanel'
import { Sidebar } from './components/layout/Sidebar'
import { Pill } from './components/ui/Pill'
import { useAudio } from './hooks/useAudio'
import { useGame } from './hooks/useGame'
import { useGameConfig } from './hooks/useGameConfig'
import { ACCENTS } from './config/difficulties'

export default function App() {
  const { config, isStale } = useGameConfig()
  const { player, playback, volume, setVolume } = useAudio()
  const game = useGame(player, config)

  const difficultyConfig = useMemo(
    () => config.difficulties.find((entry) => entry.key === game.difficulty),
    [config.difficulties, game.difficulty],
  )
  const accent = difficultyConfig?.color ?? ACCENTS[game.difficulty]

  const isRoundOver = game.status === 'revealing'
  const autoNextIn = useAutoNextCountdown(
    isRoundOver && game.autoNext,
    config.auto_next_delay_seconds,
  )

  return (
    <div className="app" style={{ '--accent': accent } as React.CSSProperties}>
      <Sidebar
        difficulties={config.difficulties}
        selected={game.difficulty}
        onSelect={game.selectDifficulty}
        onReroll={game.reroll}
        disabled={game.isBusy || game.status === 'loading'}
        rerollCountsAsLoss={config.reroll_counts_as_loss}
      />

      <main className="app__main">
        {isStale && (
          <p className="app__banner">
            Using built-in defaults, the server config could not be loaded
          </p>
        )}

        {game.status === 'error' ? (
          <div className="app__error">
            <p className="app__error-text">{game.error}</p>
            <Pill active onClick={game.retry}>
              Try again
            </Pill>
          </div>
        ) : game.status === 'won' || game.status === 'failed' ? (
          <ResultFlash won={game.status === 'won'} />
        ) : game.status === 'revealing' && game.result ? (
          <ResultView
            result={game.result}
            difficultyConfig={difficultyConfig}
            playback={playback}
            onTogglePlayback={() => {
              if (playback.isPlaying) player.pause()
              else void player.resume()
            }}
            onNextSong={game.nextSong}
            autoNextIn={autoNextIn}
          />
        ) : (
          <GamePlayer
            round={game.round}
            stages={config.stages}
            difficulties={config.difficulties}
            difficulty={game.difficulty}
            guesses={game.guesses}
            playback={playback}
            isBusy={game.isBusy}
            lastGuessWrong={game.lastGuessWrong}
            onSelectDifficulty={game.selectDifficulty}
            onPlay={game.playCurrentClip}
            onGuess={game.submitGuess}
            onSkip={game.skip}
          />
        )}
      </main>

      <SettingsPanel
        stages={config.stages}
        currentStage={game.round?.stage_index ?? 0}
        roundActive={game.status === 'playing'}
        autoNext={game.autoNext}
        onAutoNextChange={game.setAutoNext}
        volume={volume}
        onVolumeChange={setVolume}
      />
    </div>
  )
}

/**
 * Ticks down the seconds until Auto Next fires.
 *
 * Display only. The actual transition is scheduled in useGame, so this cannot
 * drift into being a second source of truth for when the round changes.
 */
function useAutoNextCountdown(active: boolean, seconds: number): number | null {
  const [remaining, setRemaining] = useState<number | null>(null)

  useEffect(() => {
    if (!active) {
      setRemaining(null)
      return
    }

    setRemaining(Math.ceil(seconds))
    const timer = window.setInterval(() => {
      setRemaining((value) => (value === null ? null : Math.max(0, value - 1)))
    }, 1000)

    return () => window.clearInterval(timer)
  }, [active, seconds])

  return remaining
}
