import { memo, useContext, useEffect, useRef, useState } from 'react'
import { getBezierPath, type EdgeProps } from '@xyflow/react'
import { CanvasNodeContext } from './context'

/**
 * 无缝流光 — 6 个 stop 覆盖两个完整色带周期，整体平移一个周期后
 * 视觉完全一致，循环无跳变。
 * 三层晕染 + strokeLinecap round，端点自然过渡不截断。
 */

const PALETTES: Record<string, { from: string; via: string; to: string }> = {
  prompt:   { from: 'var(--color-port-prompt)', via: 'var(--color-accent)', to: 'var(--color-port-end-frame)' },
  image:    { from: 'var(--color-port-image)', via: 'var(--color-primary)', to: 'var(--color-accent)' },
  end_frame:{ from: 'var(--color-port-end-frame)', via: 'var(--color-primary)', to: 'var(--color-accent)' },
  video:    { from: 'var(--color-port-video)', via: 'var(--color-accent)', to: 'var(--color-primary)' },
  audio:    { from: 'var(--color-port-audio)', via: 'var(--color-port-end-frame)', to: 'var(--color-primary)' },
  output:   { from: 'var(--color-primary)', via: 'var(--color-accent)', to: 'var(--color-port-image)' },
}
const DEFAULT_PALETTE = PALETTES.output

/** 6 stops = 2 periods. Each stop animates offset by +1.0 (one full period),
 *  so at t=T the visual is identical to t=0 → seamless loop, no flicker. */
const STOP_TEMPLATE = [
  // period 1: spanning [-1.0, 0.0] (pre-fill, off-screen left)
  { colorKey: 'from' as const, startOffset: -0.55, opacity: '0.7' },
  { colorKey: 'via'  as const, startOffset: -0.25, opacity: '0.95' },
  { colorKey: 'to'   as const, startOffset: 0.05,  opacity: '0.7' },
  // period 2: spanning [0.25, 1.25] — drives the visible [0,1] range
  { colorKey: 'from' as const, startOffset: 0.45,  opacity: '0.7' },
  { colorKey: 'via'  as const, startOffset: 0.75,  opacity: '0.95' },
  { colorKey: 'to'   as const, startOffset: 1.05,  opacity: '0.7' },
  // period 3: spanning [1.45, 2.05] (post-fill, off-screen right) — only need the bridge
  { colorKey: 'from' as const, startOffset: 1.45,  opacity: '0.7' },
]

const FlowEdge = memo(function FlowEdge({
  id, sourceHandleId, target, sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition, selected,
}: EdgeProps) {
  const ctx = useContext(CanvasNodeContext)
  const [path] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition })

  const tStatus = ctx?.statusMap?.[String(target)]?.status
  const isRunning = tStatus === 'running' || tStatus === 'pending'

  const pathRef = useRef<SVGPathElement>(null)
  const [totalLen, setTotalLen] = useState(300)

  useEffect(() => {
    if (pathRef.current) {
      const len = pathRef.current.getTotalLength()
      if (isFinite(len) && len > 0) setTotalLen(len)
    }
  }, [path])

  const handleKey = String(sourceHandleId || '')
  const p = PALETTES[handleKey] || DEFAULT_PALETTE
  const colorMap = { from: p.from, via: p.via, to: p.to }
  const gradId = `fg-${id}`
  const glowGradId = `fgg-${id}`
  const flowDur = isRunning ? 1.5 : 3.5

  const baseWidth = selected ? 2.5 : 2
  const midWidth = isRunning ? 5 : 4
  const farWidth = isRunning ? 10 : 8

  const renderStops = (gradId: string, opacityScale: number) => (
    <linearGradient id={gradId} gradientUnits="userSpaceOnUse"
      x1={sourceX} y1={sourceY} x2={targetX} y2={targetY}>
      {STOP_TEMPLATE.map((s, i) => {
        const endOffset = s.startOffset + 1.0 // animate by exactly 1 period
        return (
          <stop key={i} stopColor={colorMap[s.colorKey]} stopOpacity={(parseFloat(s.opacity) * opacityScale).toFixed(2)}>
            <animate
              attributeName="offset"
              values={`${s.startOffset};${endOffset};${endOffset}`}
              keyTimes="0;0.95;1"
              dur={`${flowDur}s`}
              repeatCount="indefinite"
            />
          </stop>
        )
      })}
    </linearGradient>
  )

  return (
    <>
      {/* pointer 命中区 */}
      <path d={path} fill="none" stroke="transparent" strokeWidth={18} style={{ pointerEvents: 'stroke' }} />

      {/* 远层晕染 — blur(6px) */}
      <path
        d={path} fill="none"
        stroke={`url(#${glowGradId})`}
        strokeWidth={farWidth} strokeLinecap="round"
        opacity={isRunning ? 0.35 : 0.22}
        style={{ filter: 'blur(6px)' }}
      />

      {/* 中层发光 — blur(3px) */}
      <path
        d={path} fill="none"
        stroke={`url(#${gradId})`}
        strokeWidth={midWidth} strokeLinecap="round"
        opacity={isRunning ? 0.5 : 0.35}
        style={{ filter: 'blur(3px)' }}
      />

      {/* 主线 */}
      <path
        ref={pathRef}
        d={path} fill="none"
        stroke={`url(#${gradId})`}
        strokeWidth={baseWidth} strokeLinecap="round"
      />

      {/* SVG defs */}
      <svg width="0" height="0" style={{ position: 'absolute' }}>
        <defs>
          {renderStops(gradId, 1.0)}
          {renderStops(glowGradId, 0.7)}
        </defs>
      </svg>
    </>
  )
})

export default FlowEdge
