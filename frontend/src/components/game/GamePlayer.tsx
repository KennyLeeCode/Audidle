/**
 * The center column during play.
 *
 * A composer, not a container. It arranges the tabs, stage ladder, play button,
 * guess input, and skip, and owns no state of its own. Everything it renders
 * comes from useGame and useAudio via App, which is what keeps this readable
 * even though it is the busiest part of the screen.
 */

import { Pill } from '../ui/Pill'
import { SkipIcon } from '../ui/Icons'
import { DifficultyTabs } from './DifficultyTabs'
import { GuessList } from './GuessList'
import { PlayButton } from './PlayButton'
import { SongSearch } from './SongSearch'
import { StageProgress } from './StageProgress'
import type { PlaybackState } from '../../audio/types'
import type { DifficultyConfig, StageConfig } from '../../types/config'
import type { Difficulty, LocalGuess, RoundState } from '../../types/game'
import type { SongSummary } from '../../types/song'
import './GamePlayer.css'

interface GamePlayerProps {
  round: RoundState | null
  stages: StageConfig[]
  difficulties: DifficultyConfig[]
  difficulty: Difficulty
  guesses: LocalGuess[]
  playback: PlaybackState
  isBusy: boolean
  lastGuessWrong: boolean
  onSelectDifficulty: (difficulty: Difficulty) => void
  onPlay: () => void
  onGuess: (song: SongSummary) => void
  onSkip: () => void
}

export function GamePlayer({
  round,
  stages,
  difficulties,
  difficulty,
  guesses,
  playback,
  isBusy,
  lastGuessWrong,
  onSelectDifficulty,
  onPlay,
  onGuess,
  onSkip,
}: GamePlayerProps) {
  const stageIndex = round?.stage_index ?? 0
  const stage = stages[stageIndex]
  const clipDuration = round?.clip_duration ?? stage?.duration ?? 0
  const isReady = Boolean(round) && playback.isReady

  return (
    <div className="game-player">
      <DifficultyTabs
        difficulties={difficulties}
        selected={difficulty}
        onSelect={onSelectDifficulty}
        disabled={isBusy}
      />

      <StageProgress stages={stages} currentIndex={stageIndex} />

      <PlayButton
        onPlay={onPlay}
        durationLabel={stage?.label ?? `${clipDuration}s`}
        isPlaying={playback.isPlaying && playback.mode === 'clip'}
        isLoading={playback.isLoading}
        disabled={!isReady}
        clipDuration={clipDuration}
      />

      {/*
        Only shown when the active engine cannot honour the requested duration.
        Saying so is better than presenting an approximate clip as an exact one.
      */}
      {isReady && !playback.precise && clipDuration < 1 && (
        <p className="game-player__notice">
          This source cannot be clipped precisely, so short stages are approximate
        </p>
      )}

      {playback.error && <p className="game-player__error">{playback.error}</p>}

      <div className="game-player__controls">
        <SongSearch onGuess={onGuess} disabled={!round || isBusy} shake={lastGuessWrong} />
        <Pill
          className="game-player__skip"
          icon={<SkipIcon />}
          onClick={onSkip}
          disabled={!round || isBusy}
          title={
            round?.is_final_stage
              ? 'This is the last stage, skipping ends the round'
              : 'Unlock more of the same song'
          }
        >
          Skip
        </Pill>
      </div>

      <GuessList guesses={guesses} />
    </div>
  )
}
