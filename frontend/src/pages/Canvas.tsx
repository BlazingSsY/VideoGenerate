import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  addEdge, Background, BackgroundVariant, Controls,
  ReactFlow, ReactFlowProvider, type Connection, type Viewport,
  useEdgesState, useNodesState, useReactFlow,
} from '@xyflow/react'
import { nanoid } from 'nanoid'
import {
  FileText, Image as ImageIcon, Video, Music, Zap, Download,
  Plus, MoreHorizontal, Trash2, X,
  PanelLeftClose, PanelLeftOpen,
  Library, Wand2, Star, Folder,
  Sun, Moon, Save, Undo2,
} from 'lucide-react'
import api from '../api'
import { useAuth } from '../auth'
import { cn } from '../lib/utils'
import type {
  CanvasDetail, CanvasEdgeRecord, CanvasFlowEdge, CanvasFlowNode,
  CanvasNodeData, CanvasNodeRecord, CanvasSummary,
} from '../canvasTypes'
import { CanvasNodeProvider } from '../components/nodes/context'
import GenerateNode from '../components/nodes/GenerateNode'
import LibraryPanel, { type LibraryAsset } from '../components/LibraryPanel'
import ImageNode from '../components/nodes/ImageNode'
import MediaNode from '../components/nodes/MediaNode'
import OutputNode from '../components/nodes/OutputNode'
import PromptNode from '../components/nodes/PromptNode'
import FlowEdge from '../components/nodes/FlowEdge'
import { isAggregateTargetHandle, isValidCanvasConnection, sanitizeLoadedEdges } from '../canvasRules'
import type { VideoModel } from '../types'
import { useCanvasAgentBridge } from '../canvasAgentBridge'

const nodeTypes = {
  prompt: PromptNode, image: ImageNode,
  video: MediaNode, audio: MediaNode,
  generate: GenerateNode, output: OutputNode,
}
const edgeTypes = { flow: FlowEdge }

/** 画布主题：dark / light。挂 html.theme-light class，styles.css 的浅色变量组接管。 */
function useTheme() {
  const [light, setLight] = useState<boolean>(() => {
    try { return localStorage.getItem('vg-theme') === 'light' } catch { return false }
  })
  useEffect(() => {
    document.documentElement.classList.toggle('theme-light', light)
    try { localStorage.setItem('vg-theme', light ? 'light' : 'dark') } catch {}
  }, [light])
  return { light, toggle: () => setLight(v => !v) }
}

/** 侧栏小项 — unused helper removed; loadCanvas uses sanitizeLoadedEdges */

const DEFAULT_MODEL_ID = 'wan3.0-video-prime'

function draftSnapshot(nodes: CanvasFlowNode[], edges: CanvasFlowEdge[], viewport: Viewport) {
  return JSON.stringify({
    nodes: nodes.map(n => ({ id: n.id, type: n.type, position: n.position, data: n.data, status: '', message_id: null })),
    edges: edges.map(e => ({ id: e.id, source: e.source, source_handle: e.sourceHandle, target: e.target, target_handle: e.targetHandle })),
    viewport,
  })
}

const NODE_TOOLS: { type: string; label: string; icon: React.ReactNode }[] = [
  { type: 'prompt', label: '提示词', icon: <FileText className="w-4 h-4" /> },
  { type: 'image', label: '图片', icon: <ImageIcon className="w-4 h-4" /> },
  { type: 'video', label: '视频素材', icon: <Video className="w-4 h-4" /> },
  { type: 'audio', label: '音频素材', icon: <Music className="w-4 h-4" /> },
  { type: 'generate', label: '生成节点', icon: <Zap className="w-4 h-4" /> },
  { type: 'output', label: '输出', icon: <Download className="w-4 h-4" /> },
]

const EXTRA_TOOLS: { id: string; label: string; title: string; icon: React.ReactNode }[] = [
  { id: 'library', label: '素材库', title: '素材库 — 管理已上传的图片、视频和音频', icon: <Library className="w-4 h-4" /> },
  { id: 'effects', label: '特效', title: '特效 — 添加转场、滤镜等后期效果', icon: <Wand2 className="w-4 h-4" /> },
  { id: 'favorites', label: '收藏', title: '收藏 — 查看收藏的提示词和计划', icon: <Star className="w-4 h-4" /> },
  { id: 'templates', label: '模板', title: '模板 — 从预设模板快速创建画布', icon: <Folder className="w-4 h-4" /> },
]

function CanvasInner() {
  const { config } = useAuth()
  const { setBinding: setCanvasAgentBinding } = useCanvasAgentBridge()
  const [canvases, setCanvases] = useState<CanvasSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasFlowNode>([] as CanvasFlowNode[])
  const [edges, setEdges, onEdgesChange] = useEdgesState<CanvasFlowEdge>([] as CanvasFlowEdge[])
  const viewportRef = useRef<Viewport>({ x: 0, y: 0, zoom: 1 })
  const [saving, setSaving] = useState(false)
  const [title, setTitle] = useState('')
  const [statusMap, setStatusMap] = useState<Record<string, { status: string; message_id: string | null; video_src?: string; error?: string }>>({})
  const [booting, setBooting] = useState(true)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [nodeMenuOpen, setNodeMenuOpen] = useState(false)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const theme = useTheme()
  const [models, setModels] = useState<VideoModel[]>([])
  const [menuId, setMenuId] = useState<string | null>(null)
  const [renameId, setRenameId] = useState<string | null>(null)
  const [renameVal, setRenameVal] = useState('')
  const lastSaved = useRef<string>('')
  const updatedAtRef = useRef<string>('')
  const revisionRef = useRef(0)
  const [saveError, setSaveError] = useState<string | null>(null)
  const modelsRef = useRef<VideoModel[]>([])
  modelsRef.current = models
  const nodesRef = useRef(nodes)
  const edgesRef = useRef(edges)
  const activeIdRef = useRef(activeId)
  nodesRef.current = nodes
  edgesRef.current = edges
  activeIdRef.current = activeId
  const loadSequenceRef = useRef(0)
  const statusSequenceRef = useRef(0)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => {
    api.get('/api/models').then(res => setModels(res.data.models || [])).catch(() => {})
  }, [])

  const refreshStatus = useCallback(async (canvasId: string) => {
    if (!canvasId) return
    const sequence = ++statusSequenceRef.current
    try {
      const res = await api.get(`/api/canvases/${canvasId}/status`)
      if (activeIdRef.current !== canvasId || sequence !== statusSequenceRef.current) return
      setStatusMap(prev => {
        const next = { ...prev }
        for (const row of res.data as Array<{ node_id: string; status: string; message_id: string | null; video_src?: string; video_expired?: boolean; error?: string }>) {
          next[row.node_id] = {
            status: row.status,
            message_id: row.message_id,
            ...(row.video_src ? { video_src: row.video_src } : {}),
            ...(row.error ? { error: row.error } : {}),
          }
        }
        return next
      })
    } catch {}
  }, [])

  const runningRef = useRef(false)
  useEffect(() => {
    runningRef.current = Object.values(statusMap).some(s => s.status === 'pending' || s.status === 'running')
  }, [statusMap])

  useEffect(() => {
    if (!activeId || !runningRef.current) return
    const timer = setInterval(() => refreshStatus(activeId), 2000)
    return () => clearInterval(timer)
  }, [activeId, statusMap, refreshStatus])

  const loadCanvases = useCallback(async () => {
    const res = await api.get('/api/canvases')
    setCanvases(res.data)
    return res.data as CanvasSummary[]
  }, [])

  const loadCanvas = useCallback(async (id: string, preserveEdits = false) => {
    if (preserveEdits && activeIdRef.current !== id) return
    const sequence = ++loadSequenceRef.current
    const before = draftSnapshot(nodesRef.current, edgesRef.current, viewportRef.current)
    const refuseReload = () => {
      setSaveError('智能体已更新画布。本地编辑仍保留，请保存或处理版本冲突后再刷新。')
      throw new Error('本地编辑尚未同步，已保留')
    }
    if (preserveEdits && before !== lastSaved.current) refuseReload()
    const res = await api.get(`/api/canvases/${id}`)
    if (sequence !== loadSequenceRef.current) return
    if (preserveEdits) {
      if (activeIdRef.current !== id) return
      const current = draftSnapshot(nodesRef.current, edgesRef.current, viewportRef.current)
      if (current !== before || current !== lastSaved.current) refuseReload()
    }
    const d: CanvasDetail = res.data
    setTitle(d.title)
    viewportRef.current = d.viewport
    const flowNodes: CanvasFlowNode[] = d.nodes.map(n => ({
      id: n.id, type: n.type, position: n.position,
      data: n.data,
    }))
    const rawEdges: CanvasFlowEdge[] = d.edges.map(e => ({
      id: e.id, source: e.source, sourceHandle: e.source_handle,
      target: e.target, targetHandle: e.target_handle,
    }))
    // 加载期净化：修正/清除历史脏边（裸名 handle、模型切换残留的悬空槽）——
    // 它们会让后端 validate_graph 对每次保存都 400。
    const flowEdges = sanitizeLoadedEdges(rawEdges, flowNodes, modelsRef.current)
    setNodes(flowNodes)
    setEdges(flowEdges)
    setStatusMap({})
    setSaveError(null)
    // 重载后 lastSaved 需匹配 save() 的快照形状，否则首帧必然触发一次空保存
    d.nodes.forEach(n => {
      if (n.status) setStatusMap(prev => ({ ...prev, [n.id]: { status: n.status, message_id: n.message_id } }))
    })
    lastSaved.current = draftSnapshot(flowNodes, flowEdges, d.viewport)
    updatedAtRef.current = d.updated_at
    revisionRef.current = d.revision || 0
    void refreshStatus(id)
  }, [setNodes, setEdges, refreshStatus])

  useEffect(() => {
    ;(async () => {
      try {
        const list = await loadCanvases()
        if (list.length > 0) { setActiveId(list[0].id); await loadCanvas(list[0].id) }
      } catch {} finally { setBooting(false) }
    })()
  }, [loadCanvases, loadCanvas])

  const save = useCallback(async () => {
    clearTimeout(saveTimerRef.current)
    if (!activeId || activeIdRef.current !== activeId) return
    const curNodes = nodesRef.current
    const curEdges = edgesRef.current
    const serNodes = curNodes.map((n: any) => ({ id: n.id, type: n.type!, position: n.position, data: n.data, status: '', message_id: null }))
    const serEdges = curEdges.map((e: any) => ({ id: e.id, source: e.source, source_handle: e.sourceHandle!, target: e.target, target_handle: e.targetHandle! }))
    const payload = { updated_at: updatedAtRef.current, revision: revisionRef.current, viewport: viewportRef.current, nodes: serNodes, edges: serEdges }
    const snap = JSON.stringify({ nodes: serNodes, edges: serEdges, viewport: viewportRef.current })
    if (snap === lastSaved.current) return
    setSaving(true)
    try {
      const res = await api.put(`/api/canvases/${activeId}/graph`, payload)
      if (activeIdRef.current !== activeId) return
      const d = res.data as { updated_at: string; revision: number }
      updatedAtRef.current = d.updated_at
      revisionRef.current = d.revision
      lastSaved.current = snap
      setSaveError(null)
      await refreshStatus(activeId)
    } catch (err: any) {
      // 不再静默吞错：保存失败必须可见，否则用户以为已保存、刷新后回退。
      const detail = String(err?.response?.data?.detail || err?.message || '保存失败')
      if (err?.response?.status === 409) {
        // 保留本地编辑，由用户决定何时刷新；静默重载会直接丢掉未保存内容。
        setSaveError('画布已在其他窗口或被智能体更新。本地编辑仍保留，请先确认后再刷新。')
      } else {
        setSaveError(`保存失败：${detail}`)
        console.error('[canvas] save failed:', err)
      }
      throw err
    } finally { setSaving(false) }
  }, [activeId, refreshStatus])

  const debouncedSave = useCallback(() => {
    clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => { void save().catch(() => {}) }, 800)
  }, [save])
  useEffect(() => () => clearTimeout(saveTimerRef.current), [activeId])

  // ===== 回撤（undo）：nodes+edges+viewport 快照栈 =====
  interface Snap { nodes: CanvasFlowNode[]; edges: CanvasFlowEdge[]; viewport: Viewport }
  const undoStack = useRef<Snap[]>([])
  const [undoDepth, setUndoDepth] = useState(0)
  const undoLock = useRef(false)   // 恢复动作本身不再入栈
  const lastSnapSig = useRef('')

  /** pushUndo：在每一次「用户实际改动」前调用，记录改动前的状态 */
  const pushUndo = useCallback(() => {
    if (undoLock.current) return
    const snap: Snap = {
      nodes: nodesRef.current.map(n => ({ ...n, data: { ...n.data } })),
      edges: edgesRef.current.map(e => ({ ...e })),
      viewport: { ...viewportRef.current },
    }
    const sig = JSON.stringify([snap.nodes, snap.edges])
    if (sig === lastSnapSig.current) return   // 同一状态不重复入栈
    lastSnapSig.current = sig
    undoStack.current.push(snap)
    if (undoStack.current.length > 50) undoStack.current.shift()
    setUndoDepth(undoStack.current.length)
  }, [])

  const undo = useCallback(() => {
    const snap = undoStack.current.pop()
    if (!snap) return
    undoLock.current = true
    setNodes(snap.nodes)
    setEdges(snap.edges)
    setUndoDepth(undoStack.current.length)
    // 恢复后的"当前态"作为将来 undo 的对照基线
    lastSnapSig.current = JSON.stringify([snap.nodes, snap.edges])
    // 直接落库（跳过 debounce，所见即所存）
    undoLock.current = false
    debouncedSaveRef.current?.()
  }, [setNodes, setEdges])

  /** 供 undo 内部调用的 debouncedSave 引用（声明顺序问题用 ref 桥接） */
  const debouncedSaveRef = useRef<(() => void) | null>(null)
  useEffect(() => { debouncedSaveRef.current = () => debouncedSave() }, [debouncedSave])

  /** 包一层：改动前先 pushUndo，再执行原 debouncedSave —— 用于所有交互路径 */
  const pushUndoAndSave = useCallback(() => {
    pushUndo()
    debouncedSave()
  }, [pushUndo, debouncedSave])

  // ===== 保存按钮：立即保存（并允许失败提示在控制台）=====
  const saveNow = useCallback(() => { void save().catch(() => {}) }, [save])

  // 节点 data 补丁（useUpdateNodeData / setNodes）没有交互事件可挂靠，
  // 用 nodes 变更监听补上：覆盖切模型、槽位增删、输出排序等全部纯数据路径。
  const loadedOnce = useRef(false)
  useEffect(() => {
    if (!loadedOnce.current) return
    debouncedSave()
  }, [nodes, debouncedSave])
  useEffect(() => {
    if (nodes.length > 0) loadedOnce.current = true
  }, [nodes])

  const onConnect = useCallback((c: Connection) => {
    if (!isValidCanvasConnection(c, nodes, modelsRef.current)) return
    // 聚合槽（r2v 参考图片/视频/音频）允许多条边共点；prompt 与单槽类（首帧/尾帧）仍一对一
    const target = nodes.find(n => n.id === c.target)
    const multiOk = target?.type === 'generate'
      && c.targetHandle !== 'prompt'
      && isAggregateTargetHandle(c.targetHandle!, target, modelsRef.current)
    const dup = edges.some(e =>
      e.source === c.source && e.sourceHandle === c.sourceHandle
      && e.target === c.target && e.targetHandle === c.targetHandle)
    if (dup || (!multiOk && edges.some(e => e.target === c.target && e.targetHandle === c.targetHandle))) return
    pushUndo()
    setEdges(eds => addEdge({ ...c, id: nanoid() }, eds))
    debouncedSave()
  }, [nodes, edges, models, setEdges, debouncedSave, pushUndo])

  /** 素材库条目 → 画布节点（拖入或双击/+ 按钮） */
  const { screenToFlowPosition } = useReactFlow()
  const addAssetNode = (asset: LibraryAsset, at?: { x: number; y: number }) => {
    const pos = at ?? { x: 220 + Math.random() * 200, y: 160 + Math.random() * 120 }
    const isMedia = asset.kind === 'video' || asset.kind === 'audio'
    const data: any = asset.kind === 'image'
      ? { url: asset.file_url, name: asset.name, signed_url: asset.preview_url }
      : isMedia
        ? { kind: asset.kind, url: asset.file_url, name: asset.name, signed_url: asset.preview_url }
        : { label: asset.name }
    pushUndo()
    setNodes(ns => [...ns, { id: nanoid(8), type: asset.kind === 'image' ? 'image' : asset.kind, position: pos, data } as CanvasFlowNode])
    setLibraryOpen(false)
    debouncedSave()
  }
  const onAssetDrop = (e: React.DragEvent) => {
    const raw = e.dataTransfer.getData('application/x-vg-asset')
    if (!raw) return
    e.preventDefault()
    try {
      const asset = JSON.parse(raw) as LibraryAsset
      addAssetNode(asset, screenToFlowPosition({ x: e.clientX, y: e.clientY }))
    } catch { /* 非 v3 素材拖拽，忽略 */ }
  }

  const addNode = (type: string) => {
    const id = nanoid(8)
    const pos = { x: 200 + Math.random() * 200, y: 150 + Math.random() * 100 }
    const data: any = type === 'prompt' ? { text: '' }
      : type === 'image' ? { url: '', name: '' }
      : type === 'video' || type === 'audio' ? { kind: type, url: '', name: '' }
      : type === 'generate'
        ? { model: modelsRef.current.some(m => m.id === DEFAULT_MODEL_ID) ? DEFAULT_MODEL_ID : (modelsRef.current[0]?.id ?? ''), capability: 't2v', media: { video: 1 }, resolution: '1080P', ratio: '16:9', duration: 5, watermark: false, audio: true, inlinePrompt: '', media_slots: { image: ['image_0'], end_frame: ['end_frame_0'] } }
        : { label: '输出', items: [] }
    pushUndo()
    setNodes(ns => [...ns, { id, type: type as any, position: pos, data } as CanvasFlowNode])
  }

  const newCanvas = async () => {
    const res = await api.post('/api/canvases', {})
    const c: CanvasSummary = res.data
    setCanvases(prev => [c, ...prev])
    setActiveId(c.id)
    await loadCanvas(c.id)
  }

  const deleteCanvas = async (id: string) => {
    await api.delete(`/api/canvases/${id}`)
    const rest = canvases.filter(c => c.id !== id)
    setCanvases(rest)
    if (activeId === id) {
      if (rest.length > 0) { setActiveId(rest[0].id); await loadCanvas(rest[0].id) }
      else { setActiveId(null); setNodes([]); setEdges([]) }
    }
  }

  const renameCanvas = async (id: string, newTitle: string) => {
    const res = await api.patch(`/api/canvases/${id}`, { title: newTitle })
    setCanvases(prev => prev.map(c => c.id === id ? { ...c, title: newTitle } : c))
    if (activeId === id) {
      setTitle(newTitle)
      // 后端 PATCH 会 bump updated_at；同步乐观锁基准，避免下一次保存被 409 拒绝
      const d = res.data as { updated_at: string; revision: number }
      updatedAtRef.current = d.updated_at
      revisionRef.current = d.revision
    }
  }

  useEffect(() => {
    if (!activeId) {
      setCanvasAgentBinding(null)
      return
    }
    setCanvasAgentBinding({
      canvasId: activeId,
      save,
      reload: async () => { await loadCanvas(activeId, true) },
      refreshStatus: async () => { await refreshStatus(activeId) },
      takeOver: async () => {
        clearTimeout(saveTimerRef.current)
        await api.post(`/api/canvases/${activeId}/takeover`)
        await loadCanvas(activeId, true)
      },
    })
    return () => setCanvasAgentBinding(null)
  }, [activeId, loadCanvas, refreshStatus, save, setCanvasAgentBinding])

  if (booting) return <div className="flex items-center justify-center h-full bg-[var(--color-bg)]"><div className="w-8 h-8 border-2 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin" /></div>

  return (
    <div className="flex flex-1 min-h-0 relative" onDrop={onAssetDrop} onDragOver={e => { if (e.dataTransfer.types.includes('application/x-vg-asset')) e.preventDefault() }}>
      {/* Canvas list sidebar — thin striped design */}
      {sidebarOpen && (
        <aside className="w-48 shrink-0 flex flex-col border-r border-[var(--color-border)] bg-[var(--color-sidebar)]">
          <div className="flex items-center justify-between px-3 py-2.5 border-b border-[var(--color-border-soft)]">
            <span className="text-[10px] font-medium text-[var(--color-ink-tertiary)] uppercase tracking-wider">画布</span>
            <button onClick={() => setSidebarOpen(false)} className="p-1 rounded-md hover:bg-[var(--color-surface-3)] transition-colors">
              <PanelLeftClose className="w-3.5 h-3.5 text-[var(--color-ink-tertiary)]" />
            </button>
          </div>
          <div className="px-2 py-2">
            <button onClick={newCanvas} data-testid="new-canvas-btn" className="flex items-center justify-center gap-1.5 w-full py-1.5 text-xs font-medium text-white bg-brand-gradient rounded-lg hover:shadow-md transition-all">
              <Plus className="w-3 h-3" /> 新建
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-1.5">
            {canvases.map(c => (
              <div key={c.id} className={cn(
                'group relative flex items-center gap-2 px-2 py-1.5 rounded-lg border cursor-pointer mb-1 transition-colors',
                c.id === activeId ? 'bg-[var(--color-sidebar-accent)] border-[var(--color-border)]' : 'border-transparent hover:bg-[var(--color-surface-3)]'
              )} onClick={() => { setActiveId(c.id); loadCanvas(c.id) }}>
                {renameId === c.id ? (
                  <input autoFocus value={renameVal} onChange={e => setRenameVal(e.target.value)} onBlur={() => { renameCanvas(c.id, renameVal); setRenameId(null) }} onKeyDown={e => { if (e.key === 'Enter') { renameCanvas(c.id, renameVal); setRenameId(null) } }} onClick={e => e.stopPropagation()} className="flex-1 bg-transparent text-xs text-[var(--color-ink)] outline-none border-b border-[var(--color-primary)]" />
                ) : (
                  <span className="flex-1 text-xs text-[var(--color-sidebar-foreground)] truncate">{c.title}</span>
                )}
                <button onClick={e => { e.stopPropagation(); setMenuId(menuId === c.id ? null : c.id) }} className="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity shrink-0">
                  <MoreHorizontal className="w-3 h-3 text-[var(--color-ink-tertiary)]" />
                </button>
                {menuId === c.id && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={e => { e.stopPropagation(); setMenuId(null) }} />
                    <div className="absolute right-1 top-full z-50 w-28 bg-[var(--color-popover)] border border-[var(--color-border)] rounded-lg shadow-xl py-1 animate-fade-in" onClick={e => e.stopPropagation()}>
                      <button onClick={() => { setRenameId(c.id); setRenameVal(c.title); setMenuId(null) }} className="flex items-center gap-1.5 w-full px-2.5 py-1.5 text-[11px] text-[var(--color-ink-secondary)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-ink)] transition-colors">
                        <FileText className="w-2.5 h-2.5" /> 重命名
                      </button>
                      <button onClick={() => { deleteCanvas(c.id); setMenuId(null) }} className="flex items-center gap-1.5 w-full px-2.5 py-1.5 text-[11px] text-[var(--color-danger)] hover:bg-red-950/50 transition-colors">
                        <Trash2 className="w-2.5 h-2.5" /> 删除
                      </button>
                    </div>
                  </>
                )}
              </div>
            ))}
            {canvases.length === 0 && <p className="text-[11px] text-[var(--color-ink-tertiary)] px-2 py-4">还没有画布</p>}
          </div>
        </aside>
      )}
      {/* Collapsed sidebar strip — same vertical strip with expand icon at top */}
      {!sidebarOpen && (
        <button onClick={() => setSidebarOpen(true)} className="flex flex-col items-center justify-start w-10 shrink-0 border-r border-[var(--color-border)] bg-[var(--color-sidebar)] hover:bg-[var(--color-surface-3)] transition-colors pt-2.5">
          <PanelLeftOpen className="w-4 h-4 text-[var(--color-ink-tertiary)]" />
        </button>
      )}

      {/* Main canvas area */}
      <div className="relative flex-1 min-h-0 bg-[var(--color-canvas-bg)]">
        {/* 左上角：保存画布 + 回撤 */}
        {saveError && (
          <div className="absolute left-3 top-16 z-30 flex items-center gap-2 px-3 py-1.5 bg-red-950/80 backdrop-blur-sm border border-red-500/30 rounded-xl shadow-lg text-[11px] text-red-200 animate-fade-in" data-testid="save-error">
            {saveError}
            <button onClick={() => setSaveError(null)} aria-label="关闭提示" className="text-red-300 hover:text-red-100 shrink-0"><X className="w-3 h-3" /></button>
          </div>
        )}
        <div className="absolute left-3 top-3 z-20 flex items-center gap-1 p-1.5 bg-[var(--color-surface-1)]/95 backdrop-blur-sm border border-[var(--color-border)] rounded-2xl shadow-lg">
          <div className="relative tool-tip-wrap">
            <button
              onClick={saveNow}
              data-testid="save-canvas-btn"
              aria-label="保存画布"
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-xl transition-all',
                saving ? 'text-[var(--color-primary)] animate-pulse' : 'text-[var(--color-ink-secondary)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-primary)]'
              )}
            >
              <Save className="w-4 h-4" />
            </button>
            <div className="tool-tip absolute left-1/2 -translate-x-1/2 top-full mt-2 whitespace-nowrap px-2 py-1 bg-[var(--color-popover)] border border-[var(--color-border)] rounded-md text-[10px] text-[var(--color-ink-secondary)] z-50">
              保存画布
            </div>
          </div>
          <div className="relative tool-tip-wrap">
            <button
              onClick={undo}
              data-testid="undo-canvas-btn"
              aria-label="回撤"
              disabled={undoDepth === 0}
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-xl transition-all disabled:opacity-30 disabled:cursor-not-allowed',
                undoDepth > 0 ? 'text-[var(--color-ink-secondary)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-primary)]' : 'text-[var(--color-ink-tertiary)]'
              )}
            >
              <Undo2 className="w-4 h-4" />
            </button>
            <div className="tool-tip absolute left-1/2 -translate-x-1/2 top-full mt-2 whitespace-nowrap px-2 py-1 bg-[var(--color-popover)] border border-[var(--color-border)] rounded-md text-[10px] text-[var(--color-ink-secondary)] z-50">
              回撤{undoDepth > 0 ? `（${undoDepth}）` : ''}
            </div>
          </div>
        </div>
        {/* Vertical tool rail — left center overlay on canvas */}
        <div className="absolute left-3 top-1/2 -translate-y-1/2 z-20 flex flex-col items-center gap-1 p-1.5 bg-[var(--color-surface-1)]/95 backdrop-blur-sm border border-[var(--color-border)] rounded-2xl shadow-lg">
          {/* Add node button with popover */}
          <div className="relative">
            <div className="relative tool-tip-wrap">
              <button
                onClick={() => setNodeMenuOpen(!nodeMenuOpen)}
                data-testid="add-node-btn"
                className={cn(
                  'flex items-center justify-center w-9 h-9 rounded-xl transition-all',
                  nodeMenuOpen ? 'bg-[var(--color-accent-light)] text-[var(--color-accent)]' : 'hover:bg-[var(--color-surface-3)] text-[var(--color-ink-secondary)]'
                )}
              >
                <Plus className="w-4 h-4" />
              </button>
              <div className="tool-tip absolute left-full ml-2 top-1/2 -translate-y-1/2 whitespace-nowrap px-2 py-1 bg-[var(--color-popover)] border border-[var(--color-border)] rounded-md text-[10px] text-[var(--color-ink-secondary)] z-50">
                添加节点
              </div>
            </div>
            {nodeMenuOpen && (
              <>
                <div className="fixed inset-0 z-30" onClick={() => setNodeMenuOpen(false)} />
                <div className="absolute left-full ml-2 top-0 z-40 w-36 bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-xl shadow-xl py-1 animate-fade-in">
                  <p className="px-3 py-1 text-[9px] font-medium text-[var(--color-ink-tertiary)] uppercase tracking-wider">节点类型</p>
                  {NODE_TOOLS.map(t => (
                    <button key={t.type} data-testid={`node-tool-${t.type}`} onClick={() => { addNode(t.type); setNodeMenuOpen(false) }} className="flex items-center gap-2 w-full px-3 py-2 text-xs text-[var(--color-ink-secondary)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-ink)] rounded-lg transition-colors">
                      {t.icon} {t.label}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
          {/* Divider */}
          <div className="w-5 h-px bg-[var(--color-border)] my-0.5" />
          {/* Theme toggle */}
          <div className="relative tool-tip-wrap">
            <button
              onClick={theme.toggle}
              data-testid="theme-toggle"
              aria-label="切换浅色/深色主题"
              className="flex items-center justify-center w-9 h-9 rounded-xl text-[var(--color-ink-secondary)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-primary)] transition-colors"
            >
              {theme.light ? <Moon className="w-4 h-4" /> : <Sun className="w-4 h-4" />}
            </button>
            <div className="tool-tip absolute left-full ml-2 top-1/2 -translate-y-1/2 whitespace-nowrap px-2 py-1 bg-[var(--color-popover)] border border-[var(--color-border)] rounded-md text-[10px] text-[var(--color-ink-secondary)] z-50">
              {theme.light ? '切换到深色' : '切换到浅色'}
            </div>
          </div>
          {/* Extra tools */}
          {EXTRA_TOOLS.map(t => (
            <div key={t.id} className="relative tool-tip-wrap">
              <button
                onClick={() => { if (t.id === 'library') setLibraryOpen(o => !o) }}
                data-testid={`tool-${t.id}`}
                className={cn(
                  'flex items-center justify-center w-9 h-9 rounded-xl transition-colors',
                  t.id === 'library' && libraryOpen
                    ? 'bg-[var(--color-surface-3)] text-[var(--color-primary)]'
                    : 'text-[var(--color-ink-secondary)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-primary)]',
                )}>
                {t.icon}
              </button>
              <div className="tool-tip absolute left-full ml-2 top-1/2 -translate-y-1/2 whitespace-nowrap px-2 py-1 bg-[var(--color-popover)] border border-[var(--color-border)] rounded-md text-[10px] text-[var(--color-ink-secondary)] z-50">
                {t.title}
              </div>
            </div>
          ))}
        </div>

{/* ReactFlow canvas */}
        {/* Real context values — nodes read models/activeCanvasId/statusMap from here */}
        <CanvasNodeProvider value={{
          models,
          activeCanvasId: activeId,
          statusMap,
          refresh: () => activeId && refreshStatus(activeId),
        }}>
            <ReactFlow
              nodes={nodes} edges={edges}
              onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onViewportChange={(vp: Viewport) => { viewportRef.current = vp }}
              onNodesDelete={() => { pushUndo(); debouncedSave() }}
              onEdgesDelete={() => { pushUndo(); debouncedSave() }}
              onNodeDragStart={() => pushUndo()}
              onNodeDragStop={() => debouncedSave()}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              fitView
              fitViewOptions={{ maxZoom: 0.7 }}
              deleteKeyCode={["Backspace", "Delete"]}
              connectionRadius={40}
              connectionLineStyle={{ stroke: 'var(--color-accent)', strokeWidth: 2 }}
              minZoom={0.2}
              maxZoom={3}
              colorMode={theme.light ? 'light' : 'dark'}
              defaultEdgeOptions={{ type: 'flow', style: { stroke: 'var(--color-border)', strokeWidth: 2 } }}
            >
              {/* 点阵与面板共用主题色，保持卡片和连线的视觉层级。 */}
              <Background color="var(--color-canvas-dot)" gap={16} variant={BackgroundVariant.Dots} size={1.8} />
              <Controls showInteractive={false} />
            </ReactFlow>
          </CanvasNodeProvider>
        {libraryOpen && (
          <LibraryPanel
            onClose={() => setLibraryOpen(false)}
            onAddToCanvas={a => addAssetNode(a)}
          />
        )}
      </div>
    </div>
  )
}

export default function Canvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  )
}
