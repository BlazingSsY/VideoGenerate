import {
  forwardRef, useImperativeHandle, useState, useRef, useEffect,
} from 'react'
import { CheckCircle2, Loader2, Circle, XCircle, ShieldAlert } from 'lucide-react'
import { cn } from '../../lib/utils'
import ChatBubble from './ChatBubble'
import ThinkBlock from './ThinkBlock'
import StepLine from './StepLine'
import MarkdownText from './MarkdownText'
import PlanCard from './PlanCard'
import TaskList from './TaskList'

export interface AgentTranscriptHandle {
  addEvent: (type: string, data: any) => void
  reset: () => void
  clear: () => void
  hydrate: (messages: any[], runs: any[]) => void
}

interface MessageEntry {
  id: string
  role: 'user' | 'assistant'
  content: string
  steps: StepEntry[]
  thinkText: string
  plan: any | null
  planCost: number
  planSeconds: number
  planValidated: boolean
  turnId: string
  planExecuting: boolean
  planExecuted: boolean
  runId: string
  runStatus: string
  runError: string
  tasks: TaskEntry[]
  gate: GateInfo | null
  done: boolean
}

interface StepEntry {
  seq: number
  kind: string
  title: string
  status: 'running' | 'done' | 'failed'
  summary?: string
}

interface TaskEntry {
  nodeId: string
  status: string
  videoSrc?: string
  error?: string
}

interface GateInfo {
  mode: string
  cost: number
  blockedReason?: string
}

/** 首个 text.delta 的时间戳，用于 ThinkBlock 显示耗时 */
const AgentTranscript = forwardRef<
  AgentTranscriptHandle,
  {
    loading: boolean
    onExecute?: (turnId: string) => void
    onDraw?: (turnId: string) => Promise<void>
    onRefine?: () => void
    onCancelRun?: (runId: string, turnId: string) => void
    onRetryRun?: (runId: string, turnId: string) => void
  }
>(({ loading, onExecute, onDraw, onRefine, onCancelRun, onRetryRun }, ref) => {
  const [messages, setMessages] = useState<MessageEntry[]>([])
  const [currentUser, setCurrentUser] = useState<MessageEntry | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  // currentUserRef：同步镜像，避免在 setState updater 内嵌套 setState（StrictMode 会双调用导致气泡重复）
  const currentUserRef = useRef<MessageEntry | null>(null)

  const applyUser = (fn: (u: MessageEntry | null) => MessageEntry | null) => {
    currentUserRef.current = fn(currentUserRef.current)
    setCurrentUser(currentUserRef.current)
  }
  const commitUser = (patch?: (u: MessageEntry) => MessageEntry) => {
    const cur = currentUserRef.current
    if (cur) {
      const final = patch ? patch(cur) : cur
      setMessages(m => [...m, { ...final, done: true }])
    }
    currentUserRef.current = null
    setCurrentUser(null)
  }
  const applyTurn = (turnId: string, fn: (u: MessageEntry) => MessageEntry) => {
    const cur = currentUserRef.current
    if (cur && (!turnId || cur.turnId === turnId)) {
      applyUser(u => u ? fn(u) : u)
      return
    }
    if (turnId) setMessages(items => items.map(item => item.turnId === turnId ? fn(item) : item))
  }

  const scrollToBottom = () => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }
  useEffect(() => { scrollToBottom() }, [messages, currentUser])

  const blankAssistant = (): MessageEntry => ({
    id: 'a-' + Date.now(), role: 'assistant', content: '',
    steps: [], thinkText: '', plan: null, planCost: 0,
    planSeconds: 0, planValidated: false, turnId: '',
    planExecuting: false, planExecuted: false,
    runId: '', runStatus: '', runError: '', tasks: [], gate: null, done: false,
  })

  useImperativeHandle(ref, () => ({
    hydrate: (history: any[], runs: any[]) => {
      currentUserRef.current = null
      setCurrentUser(null)
      const restored: MessageEntry[] = (history || []).map((message: any) => {
        const run = message.role === 'assistant'
          ? (runs || []).find((item: any) => item.turn_id === message.turn_id)
          : null
        const plan = message.role === 'assistant' ? message.plan || null : null
        const seconds = (plan?.nodes || [])
          .filter((node: any) => node.type === 'generate')
          .reduce((sum: number, node: any) => sum + Number(node.duration || 0), 0)
        const content = String(message.content || '')
          + (run?.status === 'succeeded' && run?.video_src ? `\n\n[成片] ${run.video_src}` : '')
        return {
          id: message.id,
          role: message.role,
          content,
          steps: [], thinkText: '', plan,
          planCost: Number(message.est_cost || 0),
          planSeconds: Number(message.est_seconds ?? seconds),
          planValidated: Boolean(message.plan_validated), turnId: message.turn_id || '',
          planExecuting: false, planExecuted: Boolean(run),
          runId: run?.id || '',
          runStatus: run?.cancel_requested ? 'cancel_requested' : (run?.status || ''),
          runError: run?.error || '',
          tasks: (run?.tasks || []).map((task: any) => ({
            nodeId: task.node_id, status: task.status,
            videoSrc: task.video_src, error: task.error,
          })),
          gate: null, done: true,
        } as MessageEntry
      })
      setMessages(restored)
    },
    clear: () => {
      currentUserRef.current = null
      setCurrentUser(null)
      setMessages([])
    },
    reset: () => {
      currentUserRef.current = null
      setCurrentUser(null)
    },
    addEvent: (type: string, data: any) => {
      if (type === 'user_message') {
        // 先提交挂起的 assistant 轮，再追加 user + 空白 assistant
        const pending = currentUserRef.current
        if (pending) {
          setMessages(m => [...m, { ...pending, done: true }])
          currentUserRef.current = null
        }
        setMessages(m => [...m, {
          id: 'u-' + Date.now(), role: 'user', content: data.text,
          steps: [], thinkText: '', plan: null, planCost: 0,
          planSeconds: 0, planValidated: false, turnId: '',
          planExecuting: false, planExecuted: false,
          runId: '', runStatus: '', runError: '', tasks: [], gate: null, done: false,
        }])
        applyUser(() => blankAssistant())
        return
      }

      const inStep = (u: MessageEntry) =>
        u.steps.some(s => s.seq === data.seq)
      const upsertStep = (u: MessageEntry, entry: Partial<StepEntry> & { seq: number }): MessageEntry => ({
        ...u,
        steps: [...u.steps.filter(s => s.seq !== entry.seq), {
          seq: entry.seq,
          kind: entry.kind || '',
          title: entry.title || '',
          status: entry.status || 'running',
          summary: entry.summary,
        }],
      })

      switch (type) {
        case 'step.start':
        case 'step.replay':
          applyUser(u => u ? upsertStep(u, {
            seq: data.seq, kind: data.kind, title: data.title,
            status: type === 'step.replay' ? 'done' : 'running',
            summary: data.payload?.content ? '' : undefined,
          }) : u)
          break
        case 'step.end':
          applyUser(u => u && inStep(u)
            ? { ...u, steps: u.steps.map(s => s.seq === data.seq ? { ...s, status: data.status || 'done' } : s) }
            : u)
          break
        case 'tool.call':
          applyUser(u => u ? upsertStep(u, {
            seq: data.seq, kind: 'tool_call',
            title: data.title || data.tool || '调用工具', status: 'running',
          }) : u)
          break
        case 'tool.result':
          applyUser(u => u && inStep(u)
            ? { ...u, steps: u.steps.map(s => s.seq === data.seq
                ? { ...s, status: data.ok ? 'done' : 'failed', summary: data.summary } : s) }
            : u)
          break
        case 'skill.selected':
          applyUser(u => u ? upsertStep(u, {
            seq: data.seq, kind: 'skill',
            title: `技能：${data.label || data.id}`, status: 'done',
          }) : u)
          break
        case 'think.delta':
          applyUser(u => u ? { ...u, thinkText: u.thinkText + (data.text || '') } : u)
          break
        case 'text.delta':
          // 按 seq 幂等（重连重放时同一 seq 的 delta 已累计过则跳过）
          applyUser(u => u ? { ...u, content: u.content + (data.text || '') } : u)
          break
        case 'plan.draft':
          applyUser(u => u ? {
            ...u,
            plan: data.plan ?? u.plan,
            planCost: data.est_cost ?? u.planCost,
            planSeconds: data.est_seconds ?? u.planSeconds,
            planValidated: data.validated || u.planValidated,
            turnId: data.turn_id || u.turnId,
          } : u)
          break
        case 'plan.executing':
          applyTurn(data.turn_id || '', u => ({ ...u, planExecuting: true, runError: '' }))
          break
        case 'plan.executed':
        case 'run.started':
          applyTurn(data.turn_id || '', u => ({
            ...u, planExecuting: false, planExecuted: true,
            runId: data.run_id || u.runId,
            runStatus: u.runStatus || 'queued',
          }))
          break
        case 'plan.execute_failed':
          applyTurn(data.turn_id || '', u => ({
            ...u, planExecuting: false,
            runError: data.message || '执行失败',
          }))
          break
        case 'validate.error':
          applyUser(u => u && inStep(u)
            ? { ...u, steps: u.steps.map(s => s.seq === data.seq ? { ...s, status: 'failed', summary: data.detail } : s) }
            : u)
          break
        case 'run.task': {
          const nodeId = data.node_id || ''
          // __run__ = 整个批次收尾（附成片链接），不进 TaskList
          if (nodeId === '__run__') {
            applyTurn(data.turn_id || '', u => ({
              ...u,
              content: u.content + (data.video_src ? `\n\n[成片] ${data.video_src}` : ''),
              runStatus: data.status || u.runStatus,
              runError: data.error || u.runError,
            }))
            break
          }
          if (!nodeId) break
          applyTurn(data.turn_id || '', u => ({
            ...u,
            tasks: (() => {
              const idx = u.tasks.findIndex(t => t.nodeId === nodeId)
              const entry = { nodeId, status: data.status || 'queued', videoSrc: data.video_src, error: data.error }
              if (idx >= 0) {
                const next = [...u.tasks]
                next[idx] = entry
                return next
              }
              return [...u.tasks, entry]
            })(),
          }))
          break
        }
        case 'run.status':
          applyTurn(data.turn_id || '', u => ({
            ...u,
            runId: data.run_id || u.runId,
            runStatus: data.status || u.runStatus,
            runError: data.error || '',
          }))
          break
        case 'run.error':
        case 'run.disconnected':
          applyTurn(data.turn_id || '', u => ({
            ...u,
            runError: data.message || (type === 'run.disconnected' ? '运行进度连接中断，可刷新后恢复' : '运行失败'),
          }))
          break
        case 'run.notice':
          applyUser(u => u ? {
            ...u,
            content: u.content + `\n\n${data.message || ''}`,
          } : u)
          if (!currentUserRef.current && data.message) {
            setMessages(items => [...items, {
              ...blankAssistant(),
              id: 'notice-' + Date.now(),
              content: data.message,
              done: true,
            }])
          }
          break
        case 'gate':
          applyUser(u => u ? { ...u, gate: {
            mode: data.mode, cost: data.cost ?? 0, blockedReason: data.blocked_reason,
          } } : u)
          break
        case 'done':
          commitUser()
          break
        case 'error':
          if (!currentUserRef.current) {
            setMessages(items => [...items, { ...blankAssistant(), content: '[错误] ' + (data.message || ''), done: true }])
          } else commitUser(u => ({
            ...u,
            content: u.content + '\n\n[错误] ' + (data.message || ''),
          }))
          break
      }
    },
  }))

  const allMessages = currentUser
    ? [...messages, currentUser]
    : messages

  return (
    <div ref={scrollRef} className="h-full overflow-y-auto px-6 py-4">
      {allMessages.length === 0 && !loading && (
        <div className="flex flex-col items-center justify-center h-full text-center">
          <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-brand-gradient mb-4">
            <svg className="w-6 h-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
          </div>
          <p className="text-sm text-[var(--color-ink-secondary)] mb-1">告诉我你想做什么视频</p>
          <p className="text-xs text-[var(--color-ink-tertiary)]">我来帮你选模型、写提示词、拆镜头</p>
          <div className="flex flex-wrap gap-2 justify-center mt-6">
            {['做一个15秒日落海边的视频', 'wan3.0和HappyHorse有什么区别？', '做一个30秒的产品广告'].map(s => (
              <span key={s} className="px-3 py-1.5 text-xs rounded-full border border-[var(--color-border)] bg-[var(--color-surface-2)] text-[var(--color-ink-secondary)]">
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-4">
        {allMessages.map(msg => (
          <ChatBubble key={msg.id} role={msg.role}>
            {msg.role === 'assistant' && (
              <>
                {/* Steps */}
                {msg.steps.length > 0 && (
                  <div className="space-y-0.5 mb-2">
                    {msg.steps.map(step => (
                      <StepLine key={step.seq} step={step} />
                    ))}
                  </div>
                )}

                {/* Think block */}
                {msg.thinkText && <ThinkBlock text={msg.thinkText} />}

                {/* Content */}
                {msg.content && <MarkdownText text={msg.content} />}

                {/* Gate banner */}
                {msg.gate && (
                  <div className={cn(
                    'flex items-center gap-1.5 my-1 text-xs',
                    msg.gate.blockedReason
                      ? 'text-[var(--color-warning)]'
                      : 'text-[var(--color-ink-tertiary)]',
                  )}>
                    <ShieldAlert className="w-3 h-3" />
                    {msg.gate.blockedReason
                      ? `已切换为手动确认：${msg.gate.blockedReason}`
                      : `执行模式：${msg.gate.mode === 'auto' ? '自动执行' : '手动确认'} · 预估 ¥${msg.gate.cost?.toFixed(2) || '0.00'}`}
                  </div>
                )}

                {/* Plan card */}
                {msg.plan && (
                  <PlanCard
                    plan={msg.plan}
                    cost={msg.planCost}
                    seconds={msg.planSeconds}
                    validated={msg.planValidated}
                    turnId={msg.turnId}
                    onExecute={onExecute ? () => onExecute(msg.turnId) : undefined}
                    onDraw={onDraw ? () => onDraw(msg.turnId) : undefined}
                    onRefine={onRefine}
                    executing={msg.planExecuting}
                    executed={msg.planExecuted}
                  />
                )}

                {/* Tasks */}
                {msg.tasks.length > 0 && (
                  <TaskList
                    tasks={msg.tasks}
                    runStatus={msg.runStatus}
                    error={msg.runError}
                    onCancel={msg.runId && onCancelRun ? () => onCancelRun(msg.runId, msg.turnId) : undefined}
                    onRetry={msg.runId && onRetryRun ? () => onRetryRun(msg.runId, msg.turnId) : undefined}
                  />
                )}
                {msg.runError && msg.tasks.length === 0 && (
                  <p className="my-2 text-xs text-[var(--color-danger)]">{msg.runError}</p>
                )}

                {/* Loading indicator */}
                {!msg.done && !msg.content && !msg.thinkText && msg.steps.length === 0 && (
                  <div className="flex items-center gap-2 text-sm text-[var(--color-ink-tertiary)]">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    正在思考...
                  </div>
                )}
              </>
            )}
            {msg.role === 'user' && msg.content}
          </ChatBubble>
        ))}
      </div>
    </div>
  )
})

AgentTranscript.displayName = 'AgentTranscript'
export default AgentTranscript
