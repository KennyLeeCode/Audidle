/**
 * Right column: song start mode, the stage ladder, next-song behaviour, volume.
 *
 * The stage list here is a status display, not a control. Stages unlock by
 * playing, so clicking one would either be a cheat or a no-op. They are rendered
 * as read-only chips with three states, which is what the reference does too.
 */

import { Panel } from '../ui/Panel'
import { Pill } from '../ui/Pill'
import { RefreshIcon, TimerIcon, WaveIcon } from '../ui/Icons'
import { Toggle } from '../controls/Toggle'
import { VolumeControl } from '../controls/VolumeControl'
import type { StageConfig } from '../../types/config'
import './SettingsPanel.css'

interface SettingsPanelProps {
  stages: StageConfig[]
  currentStage: number
  /** Dimmed once the round ends, since no stage is live any more. */
  roundActive: boolean
  autoNext: boolean
  onAutoNextChange: (enabled: boolean) => void
  volume: number
  onVolumeChange: (volume: number) => void
}

export function SettingsPanel({
  stages,
  currentStage,
  roundActive,
  autoNext,
  onAutoNextChange,
  volume,
  onVolumeChange,
}: SettingsPanelProps) {
  return (
    <aside className="settings">
      <Panel title="Song start" icon={<WaveIcon />}>
        <div className="settings__stack">
          <Pill block active>
            From the start
          </Pill>
          {/*
            Declared but not implemented. The backend already models
            SongStartMode and a per-round start offset, so enabling this is a
            provider change rather than a redesign.
          */}
          <Pill block disabled title="Not implemented yet">
            Main hook
          </Pill>
        </div>
      </Panel>

      <Panel title="Stages" icon={<TimerIcon />}>
        <ul className={`stage-chips ${roundActive ? '' : 'stage-chips--inactive'}`}>
          {stages.map((stage) => {
            const state =
              stage.index < currentStage
                ? 'completed'
                : stage.index === currentStage
                  ? 'current'
                  : 'locked'
            return (
              <li
                key={stage.index}
                className={`stage-chip stage-chip--${state}`}
                aria-current={state === 'current' ? 'step' : undefined}
              >
                {stage.label}
              </li>
            )
          })}
        </ul>
      </Panel>

      <Panel title="Next song" icon={<RefreshIcon />}>
        <Toggle
          label="Auto next"
          checked={autoNext}
          onChange={onAutoNextChange}
          hint="Move to the next song automatically after the reveal"
        />
      </Panel>

      <Panel title="Volume">
        <VolumeControl volume={volume} onChange={onVolumeChange} />
      </Panel>
    </aside>
  )
}
