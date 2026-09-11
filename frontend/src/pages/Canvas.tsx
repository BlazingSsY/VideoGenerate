import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  addEdge,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  type Connection,
  type Viewport,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { nanoid } from 'nanoid'
import {
  App as AntApp,
  Alert,
  Button,
  Card,
  Divider,
  Drawer,
  Dropdown,
  Empty,
  Input,
  Layout,
  Modal,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  DeleteOutlined,
  EditOutlined,
  FileImageOutlined,
  FontSizeOutlined,
  MenuOutlined,
  MoreOutlined,
  PlusOutlined,
  SoundOutlined,
  ThunderboltOutlined,
  VideoCameraOutlined,
  ExportOutlined,
  RobotOutlined,
} from '@ant-design/icons'

import api, { errorText } from '../api'
import { useAuth } from '../auth'
import { useIsNarrow } from '../hooks/useBreakpoint'
import type {
  CanvasDetail,
  CanvasFlowEdge,
  CanvasFlowNode,
  CanvasNodeData,
  CanvasRuntime,
  CanvasSummary,
  GenerateNodeData,
} from '../canvasTypes'
import { CanvasNodeProvider } from '../components/nodes/context'
import GenerateNode from '../components/nodes/GenerateNode'
import ImageNode from '../components/nodes/ImageNode'
import MediaNode from '../components/nodes/MediaNode'
import OutputNode from '../components/nodes/OutputNode'
import PromptNode from '../components/nodes/PromptNode'
import { defaultModelParams } from '../hooks/useModelParams'
import { capabilityMediaInputs } from '../mediaCapabilities'
import type { AgentModel, AgentPlan, VideoModel } from '../types'

const { Sider, Content } = Layout
const nodeTypes = {
  prompt: PromptNode,
  image: ImageNode,
  video: MediaNode,
  audio: MediaNode,
  generate: GenerateNode,
  output: OutputNode,
}

function outputType(node?: CanvasFlowNode) {
  if (node?.type === 'prompt') return 'text'
  if (node?.type === 'image') return 'image'
  if (node?.type === 'video' || node?.type === 'generate') return 'video'
  if (node?.type === 'audio') return 'audio'
  return null
}

function inputType(node: CanvasFlowNode | undefined, handle: string | null, models: VideoModel[]) {
  if (!node || !handle) return null
  if (node.type === 'output') return handle === 'in' ? 'video' : null
  if (node.type !== 'generate') return null
  if (handle === 'prompt') return 'text'
  const data = node.data as GenerateNodeData
  const model = models.find((item) => item.id === data.model)
  const capability = model?.capabilities.find((item) => item.id === data.capability)
  if (!capability) return null
  const declared = data.media_slots
  for (const spec of capabilityMediaInputs(capability)) {
    const prefix = `${spec.kind}_`
    if (!handle.startsWith(prefix)) continue
    const index = Number(handle.slice(prefix.length))
    if (!Number.isInteger(index) || index < 0 || index >= spec.max_count) return null
    if (declared && Array.isArray(declared[spec.kind]) && !declared[spec.kind]!.includes(handle)) return null
    return spec.kind === 'end_frame' ? 'image' : spec.kind
  }
  return null
}

function CanvasWorkspace() {
  const { config } = useAuth()
  const { message } = AntApp.useApp()
  const [models, setModels] = useState<VideoModel[]>([])
  const [canvases, setCanvases] = useState<CanvasSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasFlowNode>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<CanvasFlowEdge>([])
  const [runtime, setRuntime] = useState<Record<string, CanvasRuntime>>({})
  const [viewport, setViewport] = useState<Viewport>({ x: 0, y: 0, zoom: 1 })
  const [loading, setLoading] = useState(true)
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'error' | 'conflict'>('saved')
  const [renaming, setRenaming] = useState(false)
  const [title, setTitle] = useState('')
  const isNarrow = useIsNarrow()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [agentOpen, setAgentOpen] = useState(false)
  const [agentModels, setAgentModels] = useState<AgentModel[]>([])
  const [agentModelId, setAgentModelId] = useState<string>()
  const [agentTargetDuration, setAgentTargetDuration] = useState<number | undefined>(30)
  const [agentPrompt, setAgentPrompt] = useState('')
  const [agentPlan, setAgentPlan] = useState<AgentPlan | null>(null)
  const [agentPlanning, setAgentPlanning] = useState(false)
  const [agentError, setAgentError] = useState('')
  const versionsRef = useRef<Record<string, string>>({})
  const viewportRef = useRef<Viewport>({ x: 0, y: 0, zoom: 1 })
  const activeIdRef = useRef<string | null>(null)
  const hydratingRef = useRef(false)
  const saveQueueRef = useRef<Promise<unknown>>(Promise.resolve())
  activeIdRef.current = activeId

  const refreshCanvases = useCallback(async () => {
    const response = await api.get('/api/canvases')
    setCanvases(response.data)
    return response.data as CanvasSummary[]
  }, [])

  useEffect(() => {
    ;(async () => {
      try {
        const [modelResponse, list, agentResponse] = await Promise.all([
          api.get('/api/models'), refreshCanvases(), api.get('/api/agent/models').catch(() => ({ data: [] })),
        ])
        setModels(modelResponse.data.models)
        setAgentModels(agentResponse.data || [])
        setAgentModelId(agentResponse.data?.[0]?.id)
        if (list.length) setActiveId(list[0].id)
      } catch (error) {
        message.error(errorText(error, '画布初始化失败'))
      } finally {
        setLoading(false)
      }
    })()
  }, [message, refreshCanvases])

  const applyStatus = useCallback((items: Array<CanvasRuntime & { node_id: string }>) => {
    setRuntime((current) => {
      const next = { ...current }
      for (const item of items) {
        next[item.node_id] = {
          status: item.status,
          message_id: item.message_id,
          video_src: item.video_src,
          video_expired: item.video_expired,
          error: item.error,
        }
      }
      return next
    })
  }, [])

  useEffect(() => {
    if (!activeId) {
      setNodes([])
      setEdges([])
      return
    }
    let cancelled = false
    hydratingRef.current = true
    setLoading(true)
    ;(async () => {
      try {
        const response = await api.get(`/api/canvases/${activeId}`)
        if (cancelled) return
        const detail = response.data as CanvasDetail
        setTitle(detail.title)
        const nextViewport = detail.viewport || { x: 0, y: 0, zoom: 1 }
        setViewport(nextViewport)
        viewportRef.current = nextViewport
        setNodes(detail.nodes.map((node) => ({
          id: node.id,
          type: node.type,
          position: node.position,
          data: node.data,
          width: node.size?.width,
          height: node.size?.height,
        })))
        setEdges(detail.edges.map((edge) => ({
          id: edge.id,
          source: edge.source,
          sourceHandle: edge.source_handle,
          target: edge.target,
          targetHandle: edge.target_handle,
          animated: false,
        })))
        setRuntime(Object.fromEntries(detail.nodes.map((node) => [node.id, {
          status: node.status,
          message_id: node.message_id,
        }])))
        versionsRef.current[activeId] = detail.updated_at
        setSaveState('saved')
        const status = await api.get(`/api/canvases/${activeId}/status`)
        if (!cancelled) applyStatus(status.data)
      } catch (error) {
        if (!cancelled) message.error(errorText(error, '读取画布失败'))
      } finally {
        if (!cancelled) {
          setLoading(false)
          requestAnimationFrame(() => { hydratingRef.current = false })
        }
      }
    })()
    return () => { cancelled = true }
  }, [activeId, applyStatus, message, setEdges, setNodes])

  const persistGraph = useCallback(() => {
    if (!activeId || !versionsRef.current[activeId]) return Promise.resolve()
    const canvasId = activeId
    const snapshot = {
      viewport: viewportRef.current,
      nodes: nodes.map((node) => ({
        id: node.id,
        type: node.type,
        position: node.position,
        size: node.measured?.width && node.measured?.height
          ? { width: node.measured.width, height: node.measured.height }
          : null,
        data: node.data,
      })),
      edges: edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        source_handle: edge.sourceHandle,
        target: edge.target,
        target_handle: edge.targetHandle,
      })),
    }
    const previous = saveQueueRef.current.catch(() => undefined)
    const operation = previous.then(async () => {
      if (activeIdRef.current === canvasId) setSaveState('saving')
      try {
        const response = await api.put(`/api/canvases/${canvasId}/graph`, {
          ...snapshot,
          updated_at: versionsRef.current[canvasId],
        })
        versionsRef.current[canvasId] = response.data.updated_at
        if (activeIdRef.current === canvasId) setSaveState('saved')
        setCanvases((items) => items.map((item) =>
          item.id === canvasId ? { ...item, updated_at: response.data.updated_at } : item,
        ))
      } catch (error: any) {
        if (error?.response?.status === 409) {
          if (activeIdRef.current === canvasId) setSaveState('conflict')
          message.error('画布已在其他窗口更新，请刷新后继续编辑')
        } else {
          if (activeIdRef.current === canvasId) setSaveState('error')
          message.error(errorText(error, '画布保存失败'))
        }
        throw error
      }
    })
    saveQueueRef.current = operation
    return operation
  }, [activeId, edges, message, nodes])

  useEffect(() => {
    if (hydratingRef.current || !activeId) return
    const timer = window.setTimeout(() => { persistGraph().catch(() => undefined) }, 800)
    return () => window.clearTimeout(timer)
  }, [activeId, edges, nodes, persistGraph])

  // 视口只在离开画布/切换画布时落库，纯粹平移缩放不该每次都写整张图
  useEffect(() => {
    if (!activeId) return
    const canvasId = activeId
    return () => {
      const next = viewportRef.current
      if (!versionsRef.current[canvasId]) return
      api.patch(`/api/canvases/${canvasId}`, { viewport: next })
        .then((response) => { versionsRef.current[canvasId] = response.data.updated_at })
        .catch(() => undefined)
    }
  }, [activeId])

  const { connected, incoming } = useMemo(() => {
    const connectedMap: Record<string, Set<string>> = {}
    const incomingMap: Record<string, Record<string, string>> = {}
    for (const edge of edges) {
      if (!edge.targetHandle) continue
      ;(connectedMap[edge.target] ||= new Set()).add(edge.targetHandle)
      ;(incomingMap[edge.target] ||= {})[edge.targetHandle] = edge.source
    }
    return { connected: connectedMap, incoming: incomingMap }
  }, [edges])

  const hasRunningNode = useMemo(
    () => Object.values(runtime).some((item) => item.status === 'pending' || item.status === 'running'),
    [runtime],
  )

  useEffect(() => {
    if (!hasRunningNode || !activeId) return
    const poll = async () => {
      try {
        const response = await api.get(`/api/canvases/${activeId}/status`)
        applyStatus(response.data)
      } catch {
        // A transient polling failure should not interrupt an active generation.
      }
    }
    const timer = window.setInterval(poll, 3000)
    return () => window.clearInterval(timer)
  }, [activeId, applyStatus, hasRunningNode])

  const updateNodeData = useCallback((nodeId: string, patch: Partial<CanvasNodeData>) => {
    const target = nodes.find((node) => node.id === nodeId)
    if (!target) return
    const data = { ...target.data, ...patch } as CanvasNodeData
    setNodes((current) => current.map((node) => node.id === nodeId ? { ...node, data } : node))
    if (target.type !== 'generate') return

    const generate = data as GenerateNodeData
    const model = models.find((item) => item.id === generate.model)
    const capability = model?.capabilities.find((item) => item.id === generate.capability)
    const validMediaHandles = new Set(
      capability
        ? capabilityMediaInputs(capability).flatMap((spec) =>
            Array.from({ length: spec.max_count }, (_, index) => `${spec.kind}_${index}`),
          )
        : [],
    )
    const invalid = edges.filter((edge) =>
      edge.target === nodeId &&
      edge.targetHandle !== 'prompt' &&
      Boolean(edge.targetHandle) &&
      !validMediaHandles.has(edge.targetHandle!),
    )
    if (invalid.length) {
      const invalidIds = new Set(invalid.map((edge) => edge.id))
      setEdges((current) => current.filter((edge) => !invalidIds.has(edge.id)))
      message.warning(`能力切换后已断开 ${invalid.length} 条不再适用的素材连线`)
    }
  }, [edges, message, models, nodes, setEdges, setNodes])

  const removeEdgesForHandle = useCallback((nodeId: string, handle: string) => {
    setEdges((current) => current.filter((edge) => !(edge.target === nodeId && edge.targetHandle === handle)))
  }, [setEdges])

  const runNode = useCallback(async (nodeId: string) => {
    if (!activeId) return
    try {
      await persistGraph()
      const response = await api.post(`/api/canvases/${activeId}/nodes/${nodeId}/run`)
      setRuntime((current) => ({ ...current, [nodeId]: response.data }))
    } catch (error: any) {
      if (error?.response?.status !== 409) message.error(errorText(error, '节点运行失败'))
    }
  }, [activeId, message, persistGraph])

  const isValidConnection = useCallback((connection: Connection | CanvasFlowEdge) => {
    const source = nodes.find((node) => node.id === connection.source)
    const target = nodes.find((node) => node.id === connection.target)
    if (!source || !target || source.id === target.id) return false
    if (outputType(source) !== inputType(target, connection.targetHandle ?? null, models)) return false
    if (edges.some((edge) => edge.target === target.id && edge.targetHandle === connection.targetHandle)) return false

    const adjacency = new Map<string, string[]>()
    for (const edge of edges) adjacency.set(edge.source, [...(adjacency.get(edge.source) || []), edge.target])
    adjacency.set(source.id, [...(adjacency.get(source.id) || []), target.id])
    const stack = [target.id]
    const visited = new Set<string>()
    while (stack.length) {
      const current = stack.pop()!
      if (current === source.id) return false
      if (visited.has(current)) continue
      visited.add(current)
      stack.push(...(adjacency.get(current) || []))
    }
    return true
  }, [edges, models, nodes])

  const onConnect = useCallback((connection: Connection) => {
    if (!isValidConnection(connection)) {
      message.warning('连接无效：请检查槽位类型、重复输入或环形依赖')
      return
    }
    setEdges((current) => addEdge({ ...connection, id: nanoid(), animated: false }, current))
  }, [isValidConnection, message, setEdges])

  const addNode = (type: CanvasFlowNode['type']) => {
    const model = models[0]
    if (type === 'generate' && !model) return
    let data: CanvasNodeData
    switch (type) {
      case 'prompt':
        data = { text: '' }
        break
      case 'image':
        data = { url: '', name: '图片素材' }
        break
      case 'video':
        data = { kind: 'video', url: '', name: '视频素材' }
        break
      case 'audio':
        data = { kind: 'audio', url: '', name: '音频素材' }
        break
      case 'output':
        data = { label: '视频输出' }
        break
      case 'generate':
        data = { ...defaultModelParams(model!), inlinePrompt: '' }
        break
    }
    const id = nanoid()
    setNodes((current) => {
      const inputTypes = new Set<CanvasFlowNode['type']>(['prompt', 'image', 'video', 'audio'])
      const peers = current.filter((node) =>
        inputTypes.has(type) ? inputTypes.has(node.type) : node.type === type,
      ).length
      const position = inputTypes.has(type)
        ? { x: 80, y: 80 + peers * 300 }
        : type === 'generate'
          ? { x: 470, y: 80 + peers * 560 }
          : { x: 900, y: 80 + peers * 320 }
      return [...current, { id, type, position, data }]
    })
    setRuntime((current) => ({ ...current, [id]: { status: type === 'generate' ? 'idle' : '', message_id: null } }))
  }

  const createCanvas = async () => {
    try {
      const response = await api.post('/api/canvases', {})
      setCanvases((items) => [response.data, ...items])
      setActiveId(response.data.id)
    } catch (error) {
      message.error(errorText(error, '新建画布失败'))
    }
  }

  const removeCanvas = (canvas: CanvasSummary) => {
    Modal.confirm({
      title: '删除画布',
      content: `“${canvas.title}”及其节点和生成记录都会被删除，且无法恢复。`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        await api.delete(`/api/canvases/${canvas.id}`)
        const list = await refreshCanvases()
        if (activeId === canvas.id) setActiveId(list[0]?.id || null)
      },
    })
  }

  const saveTitle = async () => {
    if (!activeId) return
    try {
      const response = await api.patch(`/api/canvases/${activeId}`, { title })
      versionsRef.current[activeId] = response.data.updated_at
      setCanvases((items) => items.map((item) => item.id === activeId ? response.data : item))
      setRenaming(false)
    } catch (error) {
      message.error(errorText(error, '重命名失败'))
    }
  }

  const askAgent = async () => {
    if (!activeId || !agentPrompt.trim()) return
    if (!agentModels.length) {
      setAgentError('当前没有可用的 Agent 模型。请在服务器 .env 中配置 AGENT_MODELS_JSON，或填写 AGENT_BASE_URL、AGENT_MODEL 和 AGENT_API_KEY。')
      return
    }
    setAgentPlanning(true)
    setAgentError('')
    setAgentPlan(null)
    try {
      const response = await api.post('/api/agent/plans', {
        surface: 'canvas', target_id: activeId, user_input: agentPrompt.trim(),
        agent_model_id: agentModelId || null, target_duration: agentTargetDuration || null,
        autonomy: 'confirm', reference_media: [],
      })
      setAgentPlan(response.data)
    } catch (error) {
      const detail = errorText(error, 'Agent 规划失败')
      setAgentError(detail)
      message.error(detail)
    }
    finally { setAgentPlanning(false) }
  }

  const applyAgentPlan = async () => {
    if (!activeId || !agentPlan) return
    try {
      const response = await api.post(`/api/agent/plans/${agentPlan.id}/accept`)
      const accepted = response.data as AgentPlan
      if (accepted.plan.nodes.some((item) => item.type === 'compose')) {
        throw new Error('当前画布暂不支持自动写入合成节点，请先在计划中使用单段视频')
      }
      const generated = accepted.plan.nodes.filter((item) => item.type === 'generate')
      const additions: CanvasFlowNode[] = []
      const newEdges: CanvasFlowEdge[] = []
      generated.forEach((item, index) => {
        if (!item.prompt || !item.model || !item.capability || !item.resolution || !item.duration) return
        const promptId = nanoid()
        const generateId = nanoid()
        additions.push({ id: promptId, type: 'prompt', position: { x: 80, y: index * 360 }, data: { text: item.prompt } })
        additions.push({ id: generateId, type: 'generate', position: { x: 470, y: index * 360 }, data: {
          model: item.model, capability: item.capability, resolution: item.resolution,
          ratio: item.ratio || '', duration: item.duration, watermark: false, audio: true,
          inlinePrompt: '', media_slots: {},
        } })
        newEdges.push({ id: nanoid(), source: promptId, sourceHandle: 'out', target: generateId, targetHandle: 'prompt' })
      })
      if (!additions.length) throw new Error('计划中没有可执行的生成节点')
      const outputId = nanoid()
      const generatedNodes = additions.filter((node) => node.type === 'generate')
      const lastGenerate = generatedNodes[generatedNodes.length - 1]
      additions.push({ id: outputId, type: 'output', position: { x: 900, y: Math.max(0, generated.length - 1) * 360 }, data: { label: accepted.plan.title || 'Agent 输出' } })
      if (lastGenerate) newEdges.push({ id: nanoid(), source: lastGenerate.id, sourceHandle: 'out', target: outputId, targetHandle: 'in' })
      setNodes((current) => [...current, ...additions])
      setEdges((current) => [...current, ...newEdges])
      setAgentPlan(null)
      setAgentPrompt('')
      message.success('计划已应用到画布')
    } catch (error) { message.error(errorText(error, '应用 Agent 计划失败')) }
  }

  const providerValue = useMemo(() => ({
    models,
    maxUploadMb: config?.max_upload_mb ?? 20,
    publicBaseUsable: config?.public_base_url_usable ?? false,
    runtime,
    connected,
    incoming,
    updateNodeData,
    removeEdgesForHandle,
    runNode,
  }), [
    config?.max_upload_mb,
    config?.public_base_url_usable,
    connected,
    incoming,
    models,
    runNode,
    runtime,
    updateNodeData,
    removeEdgesForHandle,
  ])

  if (loading && !activeId) return <div className="canvas-loading"><Spin size="large" /></div>

  const siderContent = (
    <>
      <div className="canvas-sider-head">
        <Button type="primary" className="btn-gradient" icon={<PlusOutlined />} block onClick={createCanvas}>
          新建画布
        </Button>
      </div>
      <div className="canvas-list">
        {canvases.map((canvas) => (
          <div
            key={canvas.id}
            className={'canvas-list-item' + (canvas.id === activeId ? ' active' : '')}
            onClick={() => setActiveId(canvas.id)}
          >
            <span>{canvas.title}</span>
            <Dropdown
              trigger={['click']}
              menu={{
                items: [
                  { key: 'rename', label: '重命名', icon: <EditOutlined /> },
                  { key: 'delete', label: '删除', icon: <DeleteOutlined />, danger: true },
                ],
                onClick: ({ key, domEvent }) => {
                  domEvent.stopPropagation()
                  if (key === 'delete') removeCanvas(canvas)
                  else { setActiveId(canvas.id); setTitle(canvas.title); setRenaming(true) }
                },
              }}
            >
              <MoreOutlined onClick={(event) => event.stopPropagation()} />
            </Dropdown>
          </div>
        ))}
        {!canvases.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有画布" />}
      </div>
    </>
  )

  return (
    <Layout className="canvas-page">
      {isNarrow ? (
        <Drawer
          open={drawerOpen}
          placement="left"
          width={280}
          onClose={() => setDrawerOpen(false)}
          styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' } }}
          title="画布"
          className="sider-drawer"
        >
          {siderContent}
        </Drawer>
      ) : (
        <Sider width={240} className="canvas-sider" theme="light">
          {siderContent}
        </Sider>
      )}

      <Content className="canvas-main workspace-content">
        {!activeId ? (
          <Empty className="canvas-empty-state" description="新建一张画布，开始编排创意" />
        ) : (
          <>
            <div className="canvas-toolbar">
              <div className="canvas-toolbar-title-group">
                {isNarrow && (
                  <Button size="small" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)} />
                )}
                <Typography.Text strong ellipsis className="canvas-toolbar-title">{title}</Typography.Text>
                <Tooltip title="节点和连线会在停止操作 800ms 后自动保存">
                  <span className={`canvas-save-state ${saveState}`}>
                    {saveState === 'saving' ? '保存中…' : saveState === 'saved' ? '已保存' : saveState === 'conflict' ? '版本冲突' : '保存失败'}
                  </span>
                </Tooltip>
              </div>
              <div className="canvas-toolbar-actions">
                <Tooltip title="提示词节点">
                  <Button icon={<FontSizeOutlined />} onClick={() => addNode('prompt')}>
                    <span className="canvas-btn-text">提示词</span>
                  </Button>
                </Tooltip>
                <Tooltip title="图片节点">
                  <Button icon={<FileImageOutlined />} onClick={() => addNode('image')}>
                    <span className="canvas-btn-text">图片</span>
                  </Button>
                </Tooltip>
                <Tooltip title="视频素材节点">
                  <Button icon={<VideoCameraOutlined />} onClick={() => addNode('video')}>
                    <span className="canvas-btn-text">视频</span>
                  </Button>
                </Tooltip>
                <Tooltip title="音频素材节点">
                  <Button icon={<SoundOutlined />} onClick={() => addNode('audio')}>
                    <span className="canvas-btn-text">音频</span>
                  </Button>
                </Tooltip>
                <Tooltip title={models.length ? '生成节点' : '当前账号没有可用模型'}>
                  <Button
                    type="primary"
                    className="btn-gradient"
                    icon={<ThunderboltOutlined />}
                    disabled={!models.length}
                    onClick={() => addNode('generate')}
                  >
                    <span className="canvas-btn-text">生成节点</span>
                  </Button>
                </Tooltip>
                <Tooltip title="视频输出节点：承接、预览并下载生成结果">
                  <Button icon={<ExportOutlined />} onClick={() => addNode('output')}>
                    <span className="canvas-btn-text">输出</span>
                  </Button>
                </Tooltip>
                <Tooltip title="打开 Agent 规划面板">
                  <Button icon={<RobotOutlined />} onClick={() => setAgentOpen(true)}>
                    <span className="canvas-btn-text">Agent</span>
                  </Button>
                </Tooltip>
              </div>
            </div>
            <CanvasNodeProvider value={providerValue}>
              <ReactFlow
                key={activeId}
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                isValidConnection={isValidConnection}
                onMoveEnd={(_, nextViewport) => { viewportRef.current = nextViewport }}
                defaultViewport={viewport}
                minZoom={0.2}
                maxZoom={2}
                deleteKeyCode={['Backspace', 'Delete']}
                onlyRenderVisibleElements
                fitView={!nodes.length}
              >
                <Background variant={BackgroundVariant.Dots} gap={22} size={1.3} color="#c9c6dc" />
                <MiniMap pannable zoomable className="canvas-minimap" />
                <Controls position="bottom-right" />
              </ReactFlow>
            </CanvasNodeProvider>
            {loading && <div className="canvas-overlay"><Spin /></div>}
          </>
        )}
      </Content>

      <Drawer
        title="Agent 画布规划"
        placement="right"
        width={isNarrow ? '100%' : 390}
        open={agentOpen}
        onClose={() => { setAgentOpen(false); setAgentError('') }}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          {!agentModels.length && (
            <Alert
              type="warning"
              showIcon
              message="Agent 尚未配置"
              description="请在后端 .env 中配置 AGENT_MODELS_JSON，或使用单模型兼容配置 AGENT_BASE_URL、AGENT_MODEL、AGENT_API_KEY，然后重启服务。"
            />
          )}
          {agentError && <Alert type="error" showIcon message="Agent 规划失败" description={agentError} closable onClose={() => setAgentError('')} />}
          <Select placeholder="Agent 模型" value={agentModelId} onChange={setAgentModelId} options={agentModels.map((item) => ({ value: item.id, label: item.name }))} disabled={!agentModels.length} />
          <Select allowClear placeholder="目标总时长" value={agentTargetDuration} onChange={setAgentTargetDuration} options={[15, 30, 45, 60].map((value) => ({ value, label: `${value} 秒` }))} />
          <Input.TextArea rows={5} value={agentPrompt} onChange={(event) => setAgentPrompt(event.target.value)} placeholder="描述你要编排的画布视频，例如：制作一段 45 秒的产品宣传片" />
          <Button type="primary" icon={<RobotOutlined />} loading={agentPlanning} disabled={!agentPrompt.trim() || !agentModels.length} onClick={askAgent} block>规划画布</Button>
          {agentPlan && <Card className="agent-canvas-plan" bordered={false}>
            <div className="agent-plan-heading">
              <div>
                <Typography.Text className="agent-plan-kicker">TIMELINE &amp; SHOTS</Typography.Text>
                <Typography.Title level={5}>{agentPlan.plan.title || '计划预览'}</Typography.Title>
              </div>
              <Tag color="processing">{agentPlan.plan.target_duration || agentPlan.est_seconds} 秒</Tag>
            </div>
            <Typography.Paragraph type="secondary" className="agent-plan-reason">{agentPlan.plan.reason}</Typography.Paragraph>
            <div className="agent-plan-summary">
              <span>{agentPlan.plan.nodes.filter((item) => item.type === 'generate').length} 个镜头</span>
              <span>{agentPlan.est_seconds} 秒生成时长</span>
              <span>约 ¥{agentPlan.est_cost}</span>
            </div>
            <Divider />
            <div className="agent-plan-timeline">
              {agentPlan.plan.nodes.map((item, index) => (
                <div className="agent-plan-shot" key={item.id}>
                  <div className="agent-plan-shot-marker">{String(index + 1).padStart(2, '0')}</div>
                  <div className="agent-plan-shot-body">
                    <div className="agent-plan-shot-head">
                      <strong>{item.type === 'compose' ? '最终成片 · 顺序拼接' : `Shot ${index + 1} · ${item.duration || 0} 秒`}</strong>
                      {item.type === 'generate' && item.model && <Tag>{item.model}</Tag>}
                    </div>
                    <Typography.Text>{item.prompt || (item.type === 'compose' ? `合并 ${item.inputs?.length || 0} 个镜头` : '')}</Typography.Text>
                    {item.depends_on?.length ? <Typography.Text type="secondary">依赖：{item.depends_on.join('、')}</Typography.Text> : null}
                  </div>
                </div>
              ))}
            </div>
            <Space className="agent-plan-actions">
              <Button type="primary" onClick={applyAgentPlan}>接受并应用</Button>
              <Button onClick={() => setAgentPlan(null)}>放弃</Button>
            </Space>
          </Card>}
        </Space>
      </Drawer>

      <Modal
        open={renaming}
        title="重命名画布"
        okText="保存"
        cancelText="取消"
        onOk={saveTitle}
        onCancel={() => setRenaming(false)}
      >
        <Input value={title} maxLength={120} autoFocus onChange={(event) => setTitle(event.target.value)} onPressEnter={saveTitle} />
      </Modal>
    </Layout>
  )
}

export default function Canvas() {
  return <ReactFlowProvider><CanvasWorkspace /></ReactFlowProvider>
}
