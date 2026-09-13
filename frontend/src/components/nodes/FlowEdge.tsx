import { memo, useContext } from 'react'
import { getBezierPath, type EdgeProps } from '@xyflow/react'
import { CanvasNodeContext } from './context'
import { useTheme } from '../../theme'
import DarkFlowEdge from './DarkFlowEdge'

const LIGHT_PALETTES: Record<string, [string, string, string]> = {
  prompt: ['#648ebd', '#8c80bd', '#65a6af'],
  image: ['#9380bd', '#6e98c5', '#81a4c4'],
  end_frame: ['#5ca0a8', '#7399c0', '#9486bb'],
  video: ['#b2976a', '#829ab8', '#9384b9'],
  audio: ['#62a592', '#70a4ba', '#858fc0'],
  output: ['#6e94c0', '#9785bf', '#6ca6b1'],
}

export default memo(function FlowEdge(props: EdgeProps) {
  const {
    id, sourceHandleId, target, sourceX, sourceY, targetX, targetY,
    sourcePosition, targetPosition, selected,
  } = props
  const { light } = useTheme()
  const ctx = useContext(CanvasNodeContext)
  const status = ctx?.statusMap[String(target)]?.status
  const running = status === 'running' || status === 'pending'
  const [path] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition })
  const gradient = `edge-gradient-${id}`
  const colors = LIGHT_PALETTES[String(sourceHandleId)] || LIGHT_PALETTES.output

  if (!light) return <DarkFlowEdge {...props} />

  return (
    <g className="canvas-edge" data-running={running} data-selected={!!selected}>
      <defs>
        <linearGradient id={gradient} gradientUnits="userSpaceOnUse" spreadMethod="repeat"
          x1={sourceX} y1={sourceY} x2={targetX} y2={targetY}>
          <stop offset="0" stopColor={colors[0]} />
          <stop offset="0.3333" stopColor={colors[1]} />
          <stop offset="0.6667" stopColor={colors[2]} />
          <stop offset="1" stopColor={colors[0]} />
          <animateTransform attributeName="gradientTransform" type="translate"
            from="0 0" to={`${targetX - sourceX} ${targetY - sourceY}`}
            dur={running ? '1.5s' : '3.5s'} repeatCount="indefinite" />
        </linearGradient>
      </defs>
      <path d={path} fill="none" stroke="transparent" strokeWidth={18} style={{ pointerEvents: 'stroke' }} />
      <path className="canvas-edge-halo" d={path} fill="none" stroke={`url(#${gradient})`} />
      <path className="canvas-edge-glow" d={path} fill="none" stroke={`url(#${gradient})`} />
      <path className="canvas-edge-line react-flow__edge-path" d={path} fill="none" style={{ stroke: `url(#${gradient})` }} />
    </g>
  )
})
