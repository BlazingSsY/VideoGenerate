import { useCallback, useEffect, useState } from 'react'
import {
  App as AntApp,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd'
import { DeleteOutlined, EditOutlined, FileMarkdownOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'

import api, { errorText } from '../api'
import type { PromptSkill } from '../types'

interface SkillFormValues {
  name: string
  description?: string
  instructions: string
  enabled: boolean
}

function parseSkillMarkdown(file: File, text: string): Partial<SkillFormValues> {
  const fallbackName = file.name.replace(/\.md$/i, '') || '未命名 Skill'
  const normalized = text.replace(/^\uFEFF/, '').trim()
  if (!normalized.startsWith('---')) {
    return { name: fallbackName, instructions: normalized }
  }

  const end = normalized.indexOf('\n---', 3)
  if (end < 0) return { name: fallbackName, instructions: normalized }
  const meta = normalized.slice(3, end).trim()
  const body = normalized.slice(end + 4).trim()
  const valueOf = (key: string) => {
    const match = meta.match(new RegExp(`^${key}:\\s*(.+)$`, 'im'))
    return match?.[1]?.trim().replace(/^['"]|['"]$/g, '')
  }
  return {
    name: valueOf('name') || fallbackName,
    description: valueOf('description') || '',
    instructions: body || normalized,
  }
}

export default function Skills() {
  const [skills, setSkills] = useState<PromptSkill[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<PromptSkill | null>(null)
  const [form] = Form.useForm<SkillFormValues>()
  const { message } = AntApp.useApp()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const response = await api.get('/api/skills')
      setSkills(response.data)
    } catch (error) {
      message.error(errorText(error, '加载 Skills 失败'))
    } finally {
      setLoading(false)
    }
  }, [message])

  useEffect(() => {
    load()
  }, [load])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ enabled: true })
    setOpen(true)
  }

  const openEdit = (skill: PromptSkill) => {
    setEditing(skill)
    form.setFieldsValue({
      name: skill.name,
      description: skill.description,
      instructions: skill.instructions,
      enabled: skill.enabled,
    })
    setOpen(true)
  }

  const submit = async () => {
    const values = await form.validateFields()
    setSaving(true)
    try {
      if (editing) {
        await api.patch(`/api/skills/${editing.id}`, values)
        message.success('Skill 已更新')
      } else {
        await api.post('/api/skills', values)
        message.success('Skill 已安装')
      }
      setOpen(false)
      await load()
    } catch (error) {
      message.error(errorText(error, '保存 Skill 失败'))
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (skill: PromptSkill, enabled: boolean) => {
    try {
      await api.patch(`/api/skills/${skill.id}`, { enabled })
      message.success(enabled ? 'Skill 已启用' : 'Skill 已停用')
      await load()
    } catch (error) {
      message.error(errorText(error, '更新状态失败'))
    }
  }

  const remove = async (skill: PromptSkill) => {
    try {
      await api.delete(`/api/skills/${skill.id}`)
      message.success('Skill 已卸载')
      await load()
    } catch (error) {
      message.error(errorText(error, '卸载失败'))
    }
  }

  const readMarkdown = async (file: File) => {
    try {
      const values = parseSkillMarkdown(file, await file.text())
      form.setFieldsValue(values)
      message.success('已读取 SKILL.md，请确认内容后安装')
    } catch {
      message.error('无法读取该 Markdown 文件')
    }
    return false
  }

  return (
    <div className="page-shell skills-page">
      <Card
        variant="borderless"
        className="page-card skills-card"
        title="Skills 管理"
        extra={
          <Button type="primary" className="btn-gradient" icon={<PlusOutlined />} onClick={openCreate}>
            安装 Skill
          </Button>
        }
      >
        <Typography.Paragraph type="secondary" className="skills-intro">
          安装提示词增强规则后，可在生成工作台的“文生视频”模式中按需选择；图生视频与参考生视频不会应用 Skill。
        </Typography.Paragraph>
        <Table
          rowKey="id"
          loading={loading}
          dataSource={skills}
          pagination={false}
          scroll={{ x: 760 }}
          columns={[
            {
              title: 'Skill',
              dataIndex: 'name',
              render: (name: string, skill: PromptSkill) => (
                <div className="skill-name-cell">
                  <span className="skill-icon"><FileMarkdownOutlined /></span>
                  <div>
                    <Typography.Text strong>{name}</Typography.Text>
                    <Typography.Text type="secondary">{skill.description || '暂无说明'}</Typography.Text>
                  </div>
                </div>
              ),
            },
            {
              title: '状态',
              width: 140,
              render: (_: unknown, skill: PromptSkill) => (
                <Space>
                  <Switch size="small" checked={skill.enabled} onChange={(checked) => toggle(skill, checked)} />
                  <Tag color={skill.enabled ? 'green' : 'default'}>{skill.enabled ? '已启用' : '已停用'}</Tag>
                </Space>
              ),
            },
            {
              title: '更新时间',
              dataIndex: 'updated_at',
              width: 170,
              render: (value: string) => dayjs(value).format('YYYY-MM-DD HH:mm'),
            },
            {
              title: '操作',
              width: 150,
              render: (_: unknown, skill: PromptSkill) => (
                <Space>
                  <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(skill)}>
                    编辑
                  </Button>
                  <Popconfirm
                    title="卸载这个 Skill？"
                    description="历史生成记录仍会保留 Skill 名称。"
                    okText="卸载"
                    cancelText="取消"
                    onConfirm={() => remove(skill)}
                  >
                    <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                      卸载
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        open={open}
        width={680}
        title={editing ? `编辑 Skill · ${editing.name}` : '安装提示词 Skill'}
        okText={editing ? '保存' : '安装'}
        cancelText="取消"
        confirmLoading={saving}
        onCancel={() => setOpen(false)}
        onOk={submit}
        destroyOnHidden
      >
        {!editing && (
          <div className="skill-file-import">
            <div>
              <Typography.Text strong>从本地 SKILL.md 读取</Typography.Text>
              <Typography.Text type="secondary">支持识别顶部的 name 与 description 元数据</Typography.Text>
            </div>
            <Upload accept=".md,text/markdown" showUploadList={false} beforeUpload={(file) => readMarkdown(file as File)}>
              <Button icon={<FileMarkdownOutlined />}>选择文件</Button>
            </Upload>
          </div>
        )}
        <Form form={form} layout="vertical" style={{ marginTop: 18 }}>
          <Form.Item name="name" label="Skill 名称" rules={[{ required: true, message: '请输入名称' }, { max: 80 }]}>
            <Input placeholder="例如：电影感镜头增强" />
          </Form.Item>
          <Form.Item name="description" label="功能说明" rules={[{ max: 240 }]}>
            <Input placeholder="说明这个 Skill 适合什么场景" />
          </Form.Item>
          <Form.Item
            name="instructions"
            label="提示词规则"
            rules={[{ required: true, message: '请输入 Skill 规则' }, { max: 12000 }]}
          >
            <Input.TextArea
              autoSize={{ minRows: 9, maxRows: 16 }}
              placeholder="告诉系统应该如何补充镜头、光影、动作、风格或声音等提示词细节……"
            />
          </Form.Item>
          <Form.Item name="enabled" label="安装后启用" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="停用" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
