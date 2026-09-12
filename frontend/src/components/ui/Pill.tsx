/**
 * The base pill control, used for difficulty buttons, tabs, and sidebar actions.
 *
 * Exists so the pill shape, the active accent treatment, and the press
 * animation are defined once instead of in every component that needs a button.
 */

import type { ButtonHTMLAttributes, ReactNode } from 'react'

interface PillProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean
  block?: boolean
  icon?: ReactNode
  children: ReactNode
}

export function Pill({
  active = false,
  block = false,
  icon,
  children,
  className = '',
  ...rest
}: PillProps) {
  const classes = [
    'pill',
    active ? 'pill--active' : '',
    block ? 'pill--block' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <button type="button" className={classes} aria-pressed={active} {...rest}>
      {icon}
      {children}
    </button>
  )
}
