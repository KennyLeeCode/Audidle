/** A titled settings panel, as used down the right column. */

import type { ReactNode } from 'react'

interface PanelProps {
  title: string
  icon?: ReactNode
  children: ReactNode
}

export function Panel({ title, icon, children }: PanelProps) {
  return (
    <section className="panel">
      <h2 className="panel__title">
        {icon}
        {title}
      </h2>
      {children}
    </section>
  )
}
