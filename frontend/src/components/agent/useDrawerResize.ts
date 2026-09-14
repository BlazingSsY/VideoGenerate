import { useEffect, useRef, useState, type PointerEvent, type KeyboardEvent } from 'react'

const key = 'vg-agent-drawer-width'
const bounds = () => ({ min: 320, max: Math.max(320, Math.min(900, window.innerWidth - 240)) })
const clamp = (value: number) => Math.max(bounds().min, Math.min(bounds().max, value))

export function useDrawerResize() {
  const [width, setWidth] = useState(() => {
    const saved = Number(localStorage.getItem(key))
    return clamp(Number.isFinite(saved) && saved > 0 ? saved : 380)
  })
  const drag = useRef<{ x: number; width: number } | null>(null)
  useEffect(() => {
    const resize = () => setWidth(value => clamp(value))
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [])
  useEffect(() => { localStorage.setItem(key, String(width)) }, [width])
  return {
    width,
    handleProps: {
      role: 'separator', tabIndex: 0, 'aria-label': '调整智能体宽度',
      'aria-orientation': 'vertical' as const,
      'aria-valuenow': Math.round(width), 'aria-valuemin': bounds().min, 'aria-valuemax': bounds().max,
      onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
        if (event.button !== 0) return
        event.preventDefault()
        drag.current = { x: event.clientX, width }
        event.currentTarget.setPointerCapture(event.pointerId)
      },
      onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
        if (drag.current) setWidth(clamp(drag.current.width + drag.current.x - event.clientX))
      },
      onPointerUp: (event: PointerEvent<HTMLDivElement>) => {
        drag.current = null
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
      },
      onLostPointerCapture: () => { drag.current = null },
      onPointerCancel: () => { drag.current = null },
      onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
        event.preventDefault()
        setWidth(value => clamp(value + (event.key === 'ArrowLeft' ? 24 : -24)))
      },
      onDoubleClick: () => setWidth(clamp(380)),
    },
  }
}
