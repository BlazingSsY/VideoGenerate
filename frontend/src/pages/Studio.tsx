import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  App as AntApp,
  Button,
  Drawer,
  Dropdown,
  Form,
  Input,
  Layout,
  Modal,
  Spin,
  Tooltip,
  Typography,
} from 'antd'
import {
  DeleteOutlined,
  EditOutlined,
  InfoCircleOutlined,
  MenuOutlined,
  MoreOutlined,
  PlusOutlined,
} from '@ant-design/icons'

import api, { errorText } from '../api'
import { useAuth } from '../auth'
import { useIsNarrow } from '../hooks/useBreakpoint'
import Composer, { stateForModel } from '../components/Composer'
import type { ComposerState, RefMedia } from '../components/Composer'
import MessageList from '../components/MessageList'
import ModelInfo from '../components/ModelInfo'
import type { AgentPlan, Conversation, MatrixRow, Message, ModeId, PromptSkill, VideoModel } from '../types'

const { Sider, Content } = Layout

export default function Studio() {
  const { config } = useAuth()
  const { message: toast } = AntApp.useApp()

  const [models, setModels] = useState<VideoModel[]>([])
  const [skills, setSkills] = useState<PromptSkill[]>([])
  const [matrix, setMatrix] = useState<MatrixRow[]>([])
  const [modeLabels, setModeLabels] = useState<Record<string, string>>({})
  const [maxDuration, setMaxDuration] = useState(15)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [state, setState] = useState<ComposerState | null>(null)
  const [sending, setSending] = useState(false)
  const [agentPlan, setAgentPlan] = useState<AgentPlan | null>(null)
  const [planning, setPlanning] = useState(false)
  const [booting, setBooting] = useState(true)
  const [infoOpen, setInfoOpen] = useState(false)
  const [renaming, setRenaming] = useState<Conversation | null>(null)
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const [renameForm] = Form.useForm()
  const isNarrow = useIsNarrow()
  const [drawerOpen, setDrawerOpen] = useState(false)

  const bodyRef = useRef<HTMLDivElement>(null)
  const messagesRef = useRef(messages)
  const submitRef = useRef<(override?: Partial<ComposerState>) => Promise<void>>(async () => undefined)
  messagesRef.current = messages

  const patch = useCallback((next: Partial<ComposerState>) => {
    setState((prev) => (prev ? { ...prev, ...next } : prev))
  }, [])

  const loadConversations = useCallback(async () => {
    const res = await api.get('/api/conversations')
    setConversations(res.data)
    return res.data as Conversation[]
  }, [])

  const loadMessages = useCallback(async (conversationId: string) => {
    const res = await api.get('/api/conversations/' + conversationId + '/messages')
    setMessages(res.data)
  }, [])

  useEffect(() => {
    ;(async () => {
      try {
        const [modelRes, matrixRes, skillRes, convs] = await Promise.all([
          api.get('/api/models'),
          api.get('/api/models/matrix'),
          api.get('/api/skills'),
          loadConversations(),
        ])
        const list: VideoModel[] = modelRes.data.models
        setModels(list)
        setMaxDuration(modelRes.data.max_duration)
        setModeLabels(modelRes.data.mode_labels || {})
        setMatrix(matrixRes.data.rows || [])
        setSkills((skillRes.data || []).filter((skill: PromptSkill) => skill.enabled))
        if (list.length > 0) setState(stateForModel(list[0]))
        if (convs.length > 0) setActiveId(convs[0].id)
      } catch (error) {
        toast.error(errorText(error, '初始化失败'))
      } finally {
        setBooting(false)
      }
    })()
  }, [loadConversations, toast])

  useEffect(() => {
    if (!activeId) {
      setMessages([])
      return
    }
    loadMessages(activeId).catch(() => undefined)
  }, [activeId, loadMessages])

  const pending = useMemo(
    () => messages.some((m) => m.status === 'pending' || m.status === 'running'),
    [messages],
  )

  useEffect(() => {
    if (!pending || !activeId) return
    const timer = setInterval(() => {
      loadMessages(activeId).catch(() => undefined)
    }, 3000)
    return () => clearInterval(timer)
  }, [pending, activeId, loadMessages])

  useEffect(() => {
    const el = bodyRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length])

  const submit = async (override?: Partial<ComposerState>) => {
    if (!state) return
    const payloadState = { ...state, ...override }
    const prompt = payloadState.prompt.trim()
    if (!prompt) return

    setSending(true)
    try {
      let conversationId = activeId
      if (!conversationId) {
        const created = await api.post('/api/conversations', { model: payloadState.model })
        conversationId = created.data.id as string
        setActiveId(conversationId)
      }

      const model = models.find((m) => m.id === payloadState.model)
      await api.post('/api/conversations/' + conversationId + '/generate', {
        prompt,
        model: payloadState.model,
        capability: payloadState.capability,
        resolution: payloadState.resolution,
        ratio: payloadState.ratio,
        duration: payloadState.duration,
        watermark: model?.supports_watermark ? payloadState.watermark : null,
        audio: model?.supports_audio ? payloadState.audio : null,
        reference_media: payloadState.refMedia.map(({ kind, url, name }) => ({ kind, url, name })),
        use_context: payloadState.useContext,
        skill_id: payloadState.capability === 't2v' ? payloadState.skillId || null : null,
      })

      patch({ prompt: '' })
      await Promise.all([loadMessages(conversationId), loadConversations()])
    } catch (error) {
      toast.error(errorText(error, '提交失败'))
    } finally {
      setSending(false)
    }
  }

  submitRef.current = submit

  const askAgent = async () => {
    if (!state?.prompt.trim()) return
    setPlanning(true)
    try {
      const response = await api.post('/api/agent/plans', { surface: 'studio', target_id: activeId || '', user_input: state.prompt, autonomy: 'confirm', reference_media: state.refMedia })
      setAgentPlan(response.data)
    } catch (error) { toast.error(errorText(error, '智能体暂时不可用')) } finally { setPlanning(false) }
  }

  const acceptAgentPlan = async () => {
    if (!agentPlan) return
    try {
      const response = await api.post(`/api/agent/plans/${agentPlan.id}/accept`)
      const item = response.data.plan.generations[0]
      patch({ prompt: item.prompt, model: item.model, capability: item.capability, resolution: item.resolution, ratio: item.ratio, duration: item.duration, refMedia: item.reference_media || [] })
      setAgentPlan(null)
      toast.success('计划已填入生成面板，请确认后运行')
    } catch (error) { toast.error(errorText(error, '接受计划失败')) }
  }

  const retry = useCallback((assistant: Message) => {
    const currentMessages = messagesRef.current
    const index = currentMessages.findIndex((m) => m.id === assistant.id)
    const userMessage = [...currentMessages.slice(0, index)].reverse().find((m) => m.role === 'user')
    if (!userMessage) {
      toast.error('找不到对应的输入内容')
      return
    }
    const params = userMessage.params || {}
    const refMedia: RefMedia[] = userMessage.reference_media?.length
      ? userMessage.reference_media
      : (userMessage.reference_images || []).map((url, index) => ({
          kind: 'image',
          url,
          signed_url: userMessage.reference_image_urls?.[index] || url,
          name: '参考图',
        }))
    submitRef.current({
      prompt: userMessage.prompt,
      model: userMessage.model,
      capability: (params.capability as ModeId) || 't2v',
      resolution: params.resolution,
      ratio: params.ratio || '',
      duration: params.duration,
      watermark: !!params.watermark,
      audio: params.audio !== false,
      useContext: false,
      skillId: skills.some((skill) => skill.id === params.skill_id)
        ? (params.skill_id as string)
        : '',
      refMedia,
    })
  }, [skills, toast])

  const newConversation = () => {
    setActiveId(null)
    setMessages([])
    patch({ prompt: '' })
  }

  const removeConversation = async (conversation: Conversation) => {
    try {
      await api.delete('/api/conversations/' + conversation.id)
      const rest = await loadConversations()
      if (activeId === conversation.id) setActiveId(rest.length > 0 ? rest[0].id : null)
      toast.success('已删除')
    } catch (error) {
      toast.error(errorText(error, '删除失败'))
    }
  }

  const confirmRemove = (conversation: Conversation) => {
    Modal.confirm({
      title: '删除对话',
      content: '“' + conversation.title + '”及其生成记录都会被删除，且无法恢复。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => removeConversation(conversation),
    })
  }

  const openRename = (conversation: Conversation) => {
    setRenaming(conversation)
    renameForm.setFieldsValue({ title: conversation.title })
  }

  const submitRename = async () => {
    if (!renaming) return
    const { title } = await renameForm.validateFields()
    try {
      await api.patch('/api/conversations/' + renaming.id, { title: title.trim() })
      setRenaming(null)
      await loadConversations()
      toast.success('已重命名')
    } catch (error) {
      toast.error(errorText(error, '重命名失败'))
    }
  }

  if (booting) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    )
  }

  const siderContent = (
    <>
      <div style={{ padding: 12 }}>
        <Button
          type="primary"
          className="btn-gradient"
          icon={<PlusOutlined />}
          block
          onClick={newConversation}
        >
          新建对话
        </Button>
        <Button
          type="text"
          size="small"
          block
          icon={<InfoCircleOutlined />}
          style={{ marginTop: 8 }}
          onClick={() => setInfoOpen(true)}
        >
          模型能力说明
        </Button>
      </div>
      <div className="conv-list">
        {conversations.length === 0 && (
          <Typography.Text type="secondary" style={{ fontSize: 12, padding: 8, display: 'block' }}>
            还没有对话记录
          </Typography.Text>
        )}
        {conversations.map((conversation) => (
          <div
            key={conversation.id}
            className={'conv-item' + (conversation.id === activeId ? ' active' : '')}
            onClick={() => setActiveId(conversation.id)}
          >
            <Tooltip title={conversation.title} mouseEnterDelay={0.6}>
              <span className="conv-item-title">{conversation.title}</span>
            </Tooltip>
            <Dropdown
              trigger={['click']}
              open={menuOpenId === conversation.id}
              onOpenChange={(open) => setMenuOpenId(open ? conversation.id : null)}
              menu={{
                items: [
                  { key: 'rename', label: '重命名', icon: <EditOutlined /> },
                  { type: 'divider' },
                  { key: 'delete', label: '删除对话', icon: <DeleteOutlined />, danger: true },
                ],
                onClick: ({ key, domEvent }) => {
                  domEvent.stopPropagation()
                  setMenuOpenId(null)
                  if (key === 'rename') openRename(conversation)
                  else confirmRemove(conversation)
                },
              }}
            >
              <span
                className={
                  'conv-item-actions' + (menuOpenId === conversation.id ? ' open' : '')
                }
                onClick={(e) => e.stopPropagation()}
              >
                <MoreOutlined />
              </span>
            </Dropdown>
          </div>
        ))}
      </div>
    </>
  )

  return (
    <Layout style={{ flex: 1, minHeight: 0 }}>
      {isNarrow ? (
        <Drawer
          open={drawerOpen}
          placement="left"
          width={280}
          onClose={() => setDrawerOpen(false)}
          styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' } }}
          title="对话"
          className="sider-drawer"
        >
          {siderContent}
        </Drawer>
      ) : (
        <Sider width={240} className="conv-sider" theme="light">
          {siderContent}
        </Sider>
      )}

      <Content className="workspace-content" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {isNarrow && (
          <div className="narrow-bar">
            <Button size="small" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)}>
              对话列表
            </Button>
            <Typography.Text ellipsis style={{ fontSize: 13 }}>
              {conversations.find((item) => item.id === activeId)?.title || '新对话'}
            </Typography.Text>
          </div>
        )}
        <div className="chat-body" ref={bodyRef}>
          <MessageList messages={messages} models={models} onRetry={retry} />
        </div>
        {state && models.length > 0 && (
          <>
          {agentPlan && <div className="agent-plan"><strong>创作计划</strong><span>{agentPlan.plan.reason}</span><span>{agentPlan.plan.generations[0].model} · {agentPlan.est_seconds} 秒 · 约 ¥{agentPlan.est_cost}</span><Button size="small" type="primary" onClick={acceptAgentPlan}>接受并填入</Button><Button size="small" onClick={() => setAgentPlan(null)}>放弃</Button></div>}
          <div className="agent-entry"><Button loading={planning} onClick={askAgent} disabled={!state.prompt.trim()}>获取创作建议</Button></div>
          <Composer
            models={models}
            skills={skills}
            state={state}
            onChange={patch}
            onSubmit={() => submit()}
            sending={sending}
            maxDuration={maxDuration}
            maxUploadMb={config?.max_upload_mb ?? 20}
            publicBaseUsable={!!config?.public_base_url_usable}
          />
          </>
        )}
      </Content>

      <Modal
        open={!!renaming}
        title="重命名对话"
        onCancel={() => setRenaming(null)}
        onOk={submitRename}
        okText="保存"
        cancelText="取消"
        destroyOnHidden
      >
        <Form form={renameForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="title"
            label="对话名称"
            rules={[{ required: true, message: '请输入名称' }, { max: 120, message: '最多 120 字' }]}
          >
            <Input placeholder="给这个对话起个名字" onPressEnter={submitRename} autoFocus />
          </Form.Item>
        </Form>
      </Modal>

      <ModelInfo
        open={infoOpen}
        onClose={() => setInfoOpen(false)}
        models={models}
        matrix={matrix}
        modeLabels={modeLabels}
        maxDuration={maxDuration}
      />
    </Layout>
  )
}
