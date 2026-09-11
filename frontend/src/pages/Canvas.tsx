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
  Button,
  Drawer,
  Dropdown,
  Empty,
  Input,
  Layout,
  Modal,
  Space,
  Spin,
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
import type { VideoModel } from '../types'

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
  for (const spec of capabilityMediaInputs(capability)) {
    const prefix = `${spec.kind}_`
    if (!handle.startsWith(prefix)) continue
    const index = Number(handle.slice(prefix.length))
    return Number.isInteger(index) && index >= 0 && index < spec.max_count ? spec.kind : null
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
        const [modelResponse, list] = await Promise.all([api.get('/api/models'), refreshCanvases()])
        setModels(modelResponse.data.models)
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

  const providerValue = useMemo(() => ({
    models,
    maxUploadMb: config?.max_upload_mb ?? 20,
    publicBaseUsable: config?.public_base_url_usable ?? false,
    runtime,
    connected,
    incoming,
    updateNodeData,
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
              <Space size={8}>
                {isNarrow && (
                  <Button size="small" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)} />
                )}
                <Typography.Text strong ellipsis className="canvas-toolbar-title">{title}</Typography.Text>
                <Tooltip title="节点和连线会在停止操作 800ms 后自动保存">
                  <span className={`canvas-save-state ${saveState}`}>
                    {saveState === 'saving' ? '保存中…' : saveState === 'saved' ? '已保存' : saveState === 'conflict' ? '版本冲突' : '保存失败'}
                  </span>
                </Tooltip>
              </Space>
              <Space size={8} className="canvas-toolbar-actions">
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
              </Space>
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
