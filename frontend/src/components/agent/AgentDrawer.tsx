import { useState, useCallback, useRef, useEffect } from 'react'
import {
  Sparkles, X, ArrowUp, Plus, History, ChevronDown, Hand,
} from 'lucide-react'
import * as RSelect from '@radix-ui/react-select'
import { cn } from '../../lib/utils'
import AgentTranscript, { type AgentTranscriptHandle } from './AgentTranscript'
import AgentWelcome from './AgentWelcome'
import { useDrawerResize } from './useDrawerResize'
import {
  acceptTurnPlan, cancelAgentRun, createAgentTurn, errorText,
  getAgentModels, getAgentSessions, type AgentSessionSummary, getAgentSessionMessages, getAgentTurn, getSessionRuns, planToCanvas,
  retryAgentRun, subscribeAgentEvents, subscribeAgentRunEvents,
} from '../../api'

interface AgentModelInfo { id: string; name: string }
interface ActiveTurn {
  canvasId: string
  turnId: string
  sessionId: string
  userInput: string
}

const activeTurnStorageKey = (canvasId: string) => `vg-agent-active-turn-${canvasId}`

function readActiveTurn(canvasId: string): ActiveTurn | null {
  try {
    const value = JSON.parse(localStorage.getItem(activeTurnStorageKey(canvasId)) || 'null')
    if (
      value?.canvasId === canvasId
      && typeof value.turnId === 'string'
      && typeof value.sessionId === 'string'
      && typeof value.userInput === 'string'
    ) return value
  } catch { /* ignore damaged browser state */ }
  return null
}

function writeActiveTurn(turn: ActiveTurn) {
  localStorage.setItem(activeTurnStorageKey(turn.canvasId), JSON.stringify(turn))
}

function clearActiveTurn(canvasId: string, turnId?: string) {
  if (!turnId || readActiveTurn(canvasId)?.turnId === turnId) {
    localStorage.removeItem(activeTurnStorageKey(canvasId))
  }
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  canvasId: string | null
  onBeforeCanvasWrite?: () => Promise<void>
  onCanvasChanged?: () => Promise<void>
  onRunStatusChanged?: () => Promise<void>
  onTakeOver?: () => Promise<void>
}

/** Compact styled Radix Select wrapper */
function MiniSelect({ value, onValue, options, label, icon }: {
  value: string
  onValue: (v: string) => void
  options: { value: string; label: string }[]
  label: string
  icon?: React.ReactNode
}) {
  const selected = options.find(o => o.value === value)
  return (
    <RSelect.Root value={value || '__default__'} onValueChange={v => onValue(v === '__default__' ? '' : v)}>
      <RSelect.Trigger aria-label={label} title={selected?.label} className="flex items-center gap-1 min-w-0 max-w-[128px] h-8 px-1.5 text-[11px] text-[var(--color-ink-secondary)] bg-transparent rounded-lg outline-none border border-transparent hover:bg-[var(--color-surface-hover)] focus-visible:border-[var(--color-accent)]/50 transition-colors whitespace-nowrap">
        {icon}
        <span className="truncate">{selected?.label || '选择'}</span>
        <ChevronDown className="w-3 h-3 text-[var(--color-ink-tertiary)] shrink-0" />
      </RSelect.Trigger>
      <RSelect.Portal>
        <RSelect.Content
          className="overflow-hidden bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-xl shadow-xl z-[100] animate-fade-in min-w-[120px]" style={{ fontSize: "11px" }}
          position="popper" sideOffset={4}
        >
          <RSelect.Viewport className="p-1">
            {options.map(o => (
              <RSelect.Item
                key={o.value} value={o.value || '__default__'}
                className="flex items-center px-2.5 py-1.5 text-[11px] text-[var(--color-ink-secondary)] rounded-lg outline-none cursor-pointer data-[highlighted]:bg-[var(--color-surface-3)] data-[highlighted]:text-[var(--color-ink)] data-[state=checked]:text-[var(--color-primary)] transition-colors"
              >
                <RSelect.ItemText>{o.label}</RSelect.ItemText>
              </RSelect.Item>
            ))}
          </RSelect.Viewport>
        </RSelect.Content>
      </RSelect.Portal>
    </RSelect.Root>
  )
}

export default function AgentDrawer({
  open, onOpenChange, canvasId, onBeforeCanvasWrite, onCanvasChanged, onRunStatusChanged, onTakeOver,
}: Props) {
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [models, setModels] = useState<AgentModelInfo[]>([])
  const [selectedModel, setSelectedModel] = useState('')
  const [modelsReady, setModelsReady] = useState(false)
  const [modelError, setModelError] = useState('')
  const [modelAttempt, setModelAttempt] = useState(0)
  const [hasConversation, setHasConversation] = useState(false)
  const [history, setHistory] = useState<AgentSessionSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [restoreVersion, setRestoreVersion] = useState(0)
  const viewEpoch = useRef(0)
  const { width, handleProps } = useDrawerResize()
  const [autonomy, setAutonomy] = useState<'ask' | 'auto'>('ask')
  const [showHistory, setShowHistory] = useState(false)
  const abortRef = useRef<(() => void) | null>(null)
  const runAbortRef = useRef<Map<string, () => void>>(new Map())
  const subscribedRunsRef = useRef<Set<string>>(new Set())
  const transcriptRef = useRef<AgentTranscriptHandle>(null)
  const inputBaseRef = useRef<HTMLTextAreaElement | null>(null)
  const canvasIdRef = useRef(canvasId)
  const onCanvasChangedRef = useRef(onCanvasChanged)
  const onRunStatusChangedRef = useRef(onRunStatusChanged)
  canvasIdRef.current = canvasId
  onCanvasChangedRef.current = onCanvasChanged
  onRunStatusChangedRef.current = onRunStatusChanged

  const focusInput = () => { inputBaseRef.current?.focus() }

  useEffect(() => {
    let cancelled = false
    setModelsReady(false)
    setModelError('')
    getAgentModels().then((data: any) => {
      if (cancelled) return
      setModels(data || [])
      if (data?.[0]) setSelectedModel(data[0].id)
      setModelsReady(true)
    }).catch(() => {
      if (!cancelled) setModelError('模型加载失败')
    })
    return () => { cancelled = true }
  }, [modelAttempt])

  useEffect(() => {
    if (!showHistory || !canvasId) return
    let cancelled = false
    setHistoryLoading(true)
    setHistoryError('')
    getAgentSessions(canvasId).then(items => {
      if (!cancelled) setHistory(items)
    }).catch(err => {
      if (!cancelled) setHistoryError(errorText(err, '聊天记录加载失败'))
    }).finally(() => { if (!cancelled) setHistoryLoading(false) })
    return () => { cancelled = true }
  }, [showHistory, canvasId])

  const handleEvent = useCallback((eventType: string, data: any) => {
    transcriptRef.current?.addEvent(eventType, data)
  }, [])

  const followRun = useCallback((runId: string, turnId: string) => {
    if (!runId || subscribedRunsRef.current.has(runId)) return
    subscribedRunsRef.current.add(runId)
    let maxSeenSeq = 0
    let finished = false
    let attempt = 0
    const epoch = viewEpoch.current
    let stopped = false
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let stopStream: (() => void) | undefined
    const subscribe = () => {
      if (stopped || epoch !== viewEpoch.current) return
      const abort = subscribeAgentRunEvents(
        runId, maxSeenSeq,
        (eventType, data) => {
          if (stopped || epoch !== viewEpoch.current) return
          attempt = 0
          if (typeof data?.seq === 'number') maxSeenSeq = Math.max(maxSeenSeq, data.seq)
          if (eventType === 'task.status') {
            handleEvent('run.task', {
              ...data, turn_id: turnId, run_id: runId,
              node_id: data.node_id,
            })
          } else if (eventType === 'run.cancel_requested') {
            handleEvent('run.status', {
              ...data, turn_id: turnId, run_id: runId,
              status: 'cancel_requested',
            })
          } else if (eventType === 'run.status') {
            handleEvent('run.status', { ...data, turn_id: turnId, run_id: runId })
            if (data.status === 'succeeded' && data.video_src) {
              handleEvent('run.task', {
                ...data, turn_id: turnId, run_id: runId,
                node_id: '__run__',
              })
            }
          } else if (eventType === 'done') {
            finished = true
            subscribedRunsRef.current.delete(runId)
            runAbortRef.current.delete(runId)
          }
          if (eventType === 'task.status' || eventType === 'run.status') {
            void onRunStatusChangedRef.current?.().catch(() => {})
          }
        },
        () => {
          if (finished || attempt >= 5) {
            subscribedRunsRef.current.delete(runId)
            if (!finished) handleEvent('run.disconnected', { turn_id: turnId, run_id: runId })
            return
          }
          attempt += 1
          retryTimer = setTimeout(subscribe, Math.min(10000, 1000 * 2 ** (attempt - 1)))
        },
      )
      stopStream = abort
    }
    subscribe()
    runAbortRef.current.set(runId, () => { stopped = true; clearTimeout(retryTimer); stopStream?.() })
  }, [handleEvent])

  const hydrateSession = useCallback(async (
    activeCanvasId: string,
    activeSessionId: string,
    omitTurnId = '',
  ) => {
    const epoch = viewEpoch.current
    const [messages, runs] = await Promise.all([
      getAgentSessionMessages(activeSessionId),
      getSessionRuns(activeSessionId),
    ])
    if (canvasIdRef.current !== activeCanvasId || epoch !== viewEpoch.current) return false
    transcriptRef.current?.hydrate(
      omitTurnId ? messages.filter(message => message.turn_id !== omitTurnId) : messages,
      runs,
    )
    for (const run of runs) {
      if (run.status === 'queued' || run.status === 'running' || run.cancel_requested) {
        followRun(run.id, run.turn_id)
      }
    }
    return true
  }, [followRun])

  const subscribeTurn = useCallback((activeTurn: ActiveTurn, hydrateOnDone = false) => {
    abortRef.current?.()
    const epoch = viewEpoch.current
    let stopped = false
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let stopStream: (() => void) | undefined
    let maxSeenSeq = 0
    let finished = false
    let attempt = 0
    const subscribe = () => {
      if (stopped || epoch !== viewEpoch.current || canvasIdRef.current !== activeTurn.canvasId) return
      stopStream = subscribeAgentEvents(
        activeTurn.turnId, maxSeenSeq,
        (eventType, data) => {
          if (stopped || epoch !== viewEpoch.current) return
          attempt = 0
          if (typeof data?.seq === 'number' && data.seq > maxSeenSeq) maxSeenSeq = data.seq
          const terminal = eventType === 'done' || eventType === 'error'
          if (terminal) {
            finished = true
            clearActiveTurn(activeTurn.canvasId, activeTurn.turnId)
          }
          if (stopped || epoch !== viewEpoch.current || canvasIdRef.current !== activeTurn.canvasId) return
          if (terminal) setLoading(false)
          if (eventType === 'session.snapshot') {
            transcriptRef.current?.hydrate(data.messages || [], data.runs || [])
            for (const run of data.runs || []) {
              if (run.status === 'queued' || run.status === 'running' || run.cancel_requested) followRun(run.id, run.turn_id)
            }
          } else if (eventType === 'run.started' && data?.run_id) {
            handleEvent('plan.executed', { turn_id: activeTurn.turnId, run_id: data.run_id })
            void onCanvasChangedRef.current?.().catch(() => {})
            followRun(data.run_id, activeTurn.turnId)
          } else if (eventType === 'canvas.changed') {
            void onCanvasChangedRef.current?.().catch(() => {})
          }
          handleEvent(eventType, data)
          if (eventType === 'done' && hydrateOnDone) {
            void hydrateSession(activeTurn.canvasId, activeTurn.sessionId).catch(() => {})
          }
        },
        () => {
          if (stopped || epoch !== viewEpoch.current || canvasIdRef.current !== activeTurn.canvasId) return
          if (finished || attempt >= 3) {
            if (!finished) {
              setLoading(false)
              handleEvent('error', { message: '连接中断，请重试' })
            }
            return
          }
          attempt += 1
          retryTimer = setTimeout(subscribe, 1000 * 2 ** (attempt - 1))
        },
      )
    }
    subscribe()
    abortRef.current = () => { stopped = true; clearTimeout(retryTimer); stopStream?.() }
  }, [followRun, handleEvent, hydrateSession])

  const send = async (text?: string) => {
    const msg = (text || input).trim()
    if (!msg || loading || !modelsReady) return
    if (!canvasId) {
      transcriptRef.current?.addEvent('error', { message: '请先创建或选择一个画布' })
      return
    }
    const originCanvasId = canvasId
    const originSessionId = sessionId
    const epoch = viewEpoch.current
    setInput('')
    setLoading(true)
    setHasConversation(true)
    setShowHistory(false)
    transcriptRef.current?.reset()
    transcriptRef.current?.addEvent('user_message', { text: msg })
    try {
      await onBeforeCanvasWrite?.()
      if (epoch !== viewEpoch.current || canvasIdRef.current !== originCanvasId) return
      const result = await createAgentTurn({
        surface: 'canvas', target_id: originCanvasId, user_input: msg, autonomy,
        agent_model_id: selectedModel || null, session_id: originSessionId,
      })
      const activeTurn: ActiveTurn = {
        canvasId: originCanvasId,
        turnId: result.id,
        sessionId: result.session_id,
        userInput: msg,
      }
      if (canvasIdRef.current !== originCanvasId) {
        // Keep the background turn recoverable without touching the new view.
        if (localStorage.getItem(`vg-agent-session-${originCanvasId}`) === originSessionId) {
          localStorage.setItem(`vg-agent-session-${originCanvasId}`, result.session_id)
          writeActiveTurn(activeTurn)
        }
        return
      }
      if (epoch !== viewEpoch.current || canvasIdRef.current !== originCanvasId) return
      localStorage.setItem(`vg-agent-session-${originCanvasId}`, result.session_id)
      writeActiveTurn(activeTurn)
      setSessionId(result.session_id)
      subscribeTurn(activeTurn)
    } catch (err) {
      if (canvasIdRef.current === originCanvasId && epoch === viewEpoch.current) {
        transcriptRef.current?.addEvent('error', { message: errorText(err, '智能体暂时不可用') })
        setLoading(false)
      }
    }
  }

  const executePlan = async (turnId: string) => {
    transcriptRef.current?.addEvent('plan.executing', { turn_id: turnId })
    try {
      await onBeforeCanvasWrite?.()
      const result = await acceptTurnPlan(turnId)
      await onCanvasChanged?.()
      transcriptRef.current?.addEvent('plan.executed', { turn_id: turnId, run_id: result.run_id })
      if (result.run_id) followRun(result.run_id, turnId)
    } catch (err: any) {
      transcriptRef.current?.addEvent('plan.execute_failed', { turn_id: turnId, message: errorText(err, '执行失败') })
    }
  }

  const drawPlan = async (turnId: string) => {
    await onBeforeCanvasWrite?.()
    await planToCanvas(turnId)
    await onCanvasChanged?.()
  }

  const cancelRun = async (runId: string, turnId: string) => {
    try {
      await cancelAgentRun(runId)
      handleEvent('run.status', { turn_id: turnId, run_id: runId, status: 'cancel_requested' })
    } catch (err) {
      handleEvent('run.error', { turn_id: turnId, run_id: runId, message: errorText(err, '取消失败') })
    }
  }

  const retryRun = async (runId: string, turnId: string) => {
    try {
      await retryAgentRun(runId)
      handleEvent('run.status', { turn_id: turnId, run_id: runId, status: 'queued' })
      followRun(runId, turnId)
    } catch (err) {
      handleEvent('run.error', { turn_id: turnId, run_id: runId, message: errorText(err, '重试失败') })
    }
  }

  const newConversation = () => {
    viewEpoch.current += 1
    setHasConversation(false)
    if (canvasId) {
      localStorage.removeItem(`vg-agent-session-${canvasId}`)
      clearActiveTurn(canvasId)
    }
    setSessionId(null)
    setInput('')
    setLoading(false)
    abortRef.current?.()
    for (const abort of runAbortRef.current.values()) abort()
    runAbortRef.current.clear()
    subscribedRunsRef.current.clear()
    transcriptRef.current?.clear()
    setShowHistory(false)
  }

  const restoreConversation = (item: AgentSessionSummary) => {
    if (!canvasId) return
    viewEpoch.current += 1
    localStorage.setItem(`vg-agent-session-${canvasId}`, item.id)
    if (item.latest_turn_status === 'draft' && item.latest_turn_id) {
      writeActiveTurn({ canvasId, sessionId: item.id, turnId: item.latest_turn_id, userInput: item.latest_user_input || item.title })
    }
    setShowHistory(false)
    setRestoreVersion(value => value + 1)
  }

  const takeOverCanvas = async () => {
    if (!onTakeOver) return
    try {
      await onBeforeCanvasWrite?.()
      await onTakeOver()
      abortRef.current?.()
      setLoading(false)
      handleEvent('run.notice', { message: '已切换为人工接手；已启动的生成任务仍会继续显示进度。' })
    } catch (err) {
      handleEvent('error', { message: errorText(err, '接管画布失败') })
    }
  }

  useEffect(() => {
    viewEpoch.current += 1
    const epoch = viewEpoch.current
    let cancelled = false
    abortRef.current?.()
    for (const abort of runAbortRef.current.values()) abort()
    runAbortRef.current.clear()
    subscribedRunsRef.current.clear()
    setSessionId(null)
    setInput('')
    setShowHistory(false)
    setHistory([])
    setHasConversation(false)
    setLoading(false)
    transcriptRef.current?.clear()

    if (!canvasId) return () => { cancelled = true }
    const storageKey = `vg-agent-session-${canvasId}`
    const savedActiveTurn = readActiveTurn(canvasId)
    const savedSessionId = localStorage.getItem(storageKey) || savedActiveTurn?.sessionId
    if (!savedSessionId) return () => { cancelled = true }
    const activeTurn = savedActiveTurn?.sessionId === savedSessionId ? savedActiveTurn : null
    if (savedActiveTurn && !activeTurn) clearActiveTurn(canvasId, savedActiveTurn.turnId)
    localStorage.setItem(storageKey, savedSessionId)

    setSessionId(savedSessionId)
    setHasConversation(true)
    setLoading(true)
    ;(async () => {
      try {
        const [messages, runs, turn] = await Promise.all([
          getAgentSessionMessages(savedSessionId),
          getSessionRuns(savedSessionId),
          activeTurn ? getAgentTurn(activeTurn.turnId) : Promise.resolve(null),
        ])
        if (cancelled || epoch !== viewEpoch.current || canvasIdRef.current !== canvasId) return
        const planning = Boolean(activeTurn && turn?.status === 'draft')
        transcriptRef.current?.hydrate(
          planning ? messages.filter(message => message.turn_id !== activeTurn?.turnId) : messages,
          runs,
        )
        for (const run of runs) {
          if (run.status === 'queued' || run.status === 'running' || run.cancel_requested) {
            followRun(run.id, run.turn_id)
          }
        }
        if (planning && activeTurn) {
          transcriptRef.current?.addEvent('user_message', {
            text: turn?.user_input || activeTurn.userInput,
            turn_id: activeTurn.turnId,
          })
          setLoading(true)
          subscribeTurn(activeTurn, true)
        } else {
          if (activeTurn) clearActiveTurn(canvasId, activeTurn.turnId)
          setLoading(false)
        }
      } catch {
        if (cancelled || epoch !== viewEpoch.current || canvasIdRef.current !== canvasId) return
        setLoading(false)
        handleEvent('run.notice', { message: '会话恢复失败，请稍后重新打开此画布重试。' })
      }
    })()

    return () => { cancelled = true }
  }, [canvasId, restoreVersion, followRun, handleEvent, subscribeTurn])

  useEffect(() => () => {
    abortRef.current?.()
    for (const abort of runAbortRef.current.values()) abort()
  }, [])

  return (
    <>
      {!open && (
      <button
        onClick={() => onOpenChange(true)}
        className="fixed bottom-6 right-6 z-50 flex items-center gap-2 px-4 py-2.5 rounded-full shadow-lg bg-brand-gradient text-white hover:shadow-xl transition-all"
      >
        <Sparkles className="w-4 h-4" />
        <span className="text-sm font-medium">智能体</span>
      </button>
      )}
    <div role="complementary" aria-label="智能体对话" style={{ '--agent-width': `${width}px`, display: open ? undefined : 'none' } as React.CSSProperties} className="agent-drawer fixed top-12 right-0 bottom-0 z-40 w-full sm:w-[var(--agent-width)] max-w-full bg-[var(--color-chat-bg)] border-l border-[var(--color-border)] flex flex-col animate-fade-in">
      <div {...handleProps} className="agent-resize-handle hidden sm:block" title="拖动调整宽度，双击恢复默认" />
      {/* Header */}
      <div className="flex items-center justify-between px-4 h-12 border-b border-[var(--color-border-soft)] shrink-0">
        <div className="flex items-center gap-2">
          <div className="flex items-center justify-center w-5 h-5 rounded-md bg-[var(--color-accent-light)]">
            <Sparkles className="w-3 h-3 text-[var(--color-accent)]" />
          </div>
          <span className="text-[13px] font-medium text-[var(--color-ink)]">{loading || sessionId ? '智能体' : '新对话'}</span>
        </div>
        <div className="flex items-center gap-1">
          {canvasId && onTakeOver && (
            <button
              onClick={() => { void takeOverCanvas() }}
              title="停止智能体操作并人工接手"
              className="flex items-center justify-center w-7 h-7 rounded-lg hover:bg-[var(--color-surface-3)] text-[var(--color-ink-tertiary)] hover:text-[var(--color-warning)] transition-colors"
            >
              <Hand className="w-3.5 h-3.5" />
            </button>
          )}
          <button onClick={newConversation} title="新建对话" className="flex items-center justify-center w-7 h-7 rounded-lg hover:bg-[var(--color-surface-3)] transition-colors">
            <Plus className="w-3.5 h-3.5 text-[var(--color-ink-tertiary)] hover:text-[var(--color-ink)]" />
          </button>
          <button onClick={() => setShowHistory(!showHistory)} title="聊天记录" className={cn(
            "flex items-center justify-center w-7 h-7 rounded-lg transition-colors",
            showHistory ? 'bg-[var(--color-surface-3)] text-[var(--color-primary)]' : 'hover:bg-[var(--color-surface-3)] text-[var(--color-ink-tertiary)] hover:text-[var(--color-ink)]'
          )}>
            <History className="w-3.5 h-3.5" />
          </button>
          <button onClick={() => onOpenChange(false)} title="关闭" className="flex items-center justify-center w-7 h-7 rounded-lg hover:bg-[var(--color-surface-3)] transition-colors">
            <X className="w-3.5 h-3.5 text-[var(--color-ink-tertiary)] hover:text-[var(--color-ink)]" />
          </button>
        </div>
      </div>

      {/* History panel */}
      {showHistory && (
        <div className="border-b border-[var(--color-border)] bg-[var(--color-surface-1)] px-3 py-2 max-h-48 overflow-y-auto animate-fade-in">
          <p className="text-[10px] font-medium text-[var(--color-ink-tertiary)] uppercase tracking-wider mb-2">聊天记录</p>
          <div className="space-y-1">
            {historyLoading ? <p className="px-2 py-1.5 text-xs text-[var(--color-ink-tertiary)]">加载中…</p>
            : historyError ? <p role="alert" className="px-2 py-1.5 text-xs text-[var(--color-danger)]">{historyError}</p>
            : history.length ? history.map(item => (
              <button key={item.id} onClick={() => restoreConversation(item)} className={cn('w-full min-w-0 px-2 py-2 text-left rounded-lg hover:bg-[var(--color-surface-3)]', item.id === sessionId && 'bg-[var(--color-surface-3)]')}>
                <span className="block truncate text-xs text-[var(--color-ink)]">{item.title || '未命名对话'}</span>
                <span className="block mt-1 text-[10px] text-[var(--color-ink-tertiary)]">{new Date(item.updated_at).toLocaleString()}{item.id === sessionId ? ' · 当前对话' : ''}</span>
              </button>
            )) : (
              <p className="text-xs text-[var(--color-ink-tertiary)] px-2 py-1.5">还没有历史记录</p>
            )}
          </div>
        </div>
      )}

      {/* Body */}
      <div className="relative flex-1 min-h-0 overflow-hidden">
        <AgentTranscript
          ref={transcriptRef}
          loading={loading}
          onExecute={executePlan}
          onDraw={drawPlan}
          onRefine={focusInput}
          onCancelRun={cancelRun}
          onRetryRun={retryRun}
        />
        {!loading && !hasConversation && (
          <div className="absolute inset-0 bg-[var(--color-chat-bg)]">
          <AgentWelcome onSelect={prompt => {
            setInput(prompt)
            focusInput()
          }} />
          </div>
        )}
      </div>

      {/* 输入、执行模式和模型整合为同一个编辑区域。 */}
      <div className="shrink-0 px-3 pt-2 pb-3">
        {!modelsReady && <div className="mb-2 text-xs text-[var(--color-ink-tertiary)]">{modelError || '正在加载模型…'}{modelError && <button onClick={() => setModelAttempt(value => value + 1)} className="ml-2 text-[var(--color-primary)]">重试</button>}</div>}
        <div className="agent-composer">
          <textarea
            ref={inputBaseRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); send() }
            }}
            aria-label="描述创意或需求"
            placeholder={canvasId ? '描述你的创意，接下来交给我…' : '请先创建或选择画布'}
            rows={3}
            className="block w-full min-h-[84px] resize-none px-1 py-1 text-[13px] bg-transparent outline-none placeholder:text-[var(--color-ink-tertiary)] text-[var(--color-ink)] leading-relaxed"
          />
          <div className="flex items-center gap-1.5 pt-2">
            <MiniSelect
              label="执行模式"
              icon={autonomy === 'ask' ? <Hand className="w-3.5 h-3.5 shrink-0" /> : <Sparkles className="w-3.5 h-3.5 shrink-0" />}
              value={autonomy}
              onValue={(v) => setAutonomy(v as 'ask' | 'auto')}
              options={[
                { value: 'ask', label: '手动确认' },
                { value: 'auto', label: '自动执行' },
              ]}
            />
            <div className="flex-1" />
            <MiniSelect
              label="智能体模型"
              value={selectedModel}
              onValue={setSelectedModel}
              options={[
                { value: '', label: models.length ? '服务器默认' : '规则规划器' },
                ...models.map(m => ({ value: m.id, label: m.name })),
              ]}
            />
            <button
              onClick={() => send()}
              disabled={!input.trim() || loading || !canvasId || !modelsReady}
              aria-label="发送消息"
              title="发送消息（Ctrl / ⌘ + Enter）"
              className={cn(
                'flex items-center justify-center w-8 h-8 rounded-full shrink-0 transition-all focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]',
                input.trim() && !loading && canvasId && modelsReady ? 'bg-brand-gradient text-white hover:shadow-md' : 'bg-[var(--color-surface-hover)] text-[var(--color-ink-tertiary)]'
              )}
            >
              <ArrowUp className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
    </>
  )
}
