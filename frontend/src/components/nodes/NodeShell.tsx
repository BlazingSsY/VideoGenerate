import type { ReactNode } from 'react'

export default function NodeShell({
  title,
  eyebrow,
  children,
  selected,
}: {
  title: string
  eyebrow: string
  children: ReactNode
  selected: boolean
}) {
  return (
    <div className={'canvas-node' + (selected ? ' selected' : '')}>
      <div className="canvas-node-head">
        <span className="canvas-node-eyebrow">{eyebrow}</span>
        <strong>{title}</strong>
      </div>
      <div className="canvas-node-body">{children}</div>
    </div>
  )
}
