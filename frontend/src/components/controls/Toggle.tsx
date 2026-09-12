/** A labelled on/off switch, as used for Auto Next. */

import './Toggle.css'

interface ToggleProps {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
  hint?: string
}

export function Toggle({ label, checked, onChange, hint }: ToggleProps) {
  return (
    <button
      type="button"
      className={`toggle ${checked ? 'toggle--on' : ''}`}
      role="switch"
      aria-checked={checked}
      title={hint}
      onClick={() => onChange(!checked)}
    >
      <span className="toggle__label">{label}</span>
      <span className="toggle__state">{checked ? 'ON' : 'OFF'}</span>
      <span className="toggle__track" aria-hidden="true">
        <span className="toggle__thumb" />
      </span>
    </button>
  )
}
