import { useCallback, useEffect, useState } from 'react'
import {
  App as AntApp,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'

import api, { errorText } from '../api'
import { useAuth } from '../auth'
import type { User } from '../types'

export default function Users() {
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<User | null>(null)
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const { message } = AntApp.useApp()
  const { user: me } = useAuth()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/api/users')
      setUsers(res.data)
    } catch (error) {
      message.error(errorText(error, '加载用户失败'))
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
    form.setFieldsValue({ role: 'user' })
    setOpen(true)
  }

  const openEdit = (record: User) => {
    setEditing(record)
    form.resetFields()
    form.setFieldsValue({
      username: record.username,
      display_name: record.display_name,
      role: record.role,
      is_active: record.is_active,
    })
    setOpen(true)
  }

  const submit = async () => {
    const values = await form.validateFields()
    try {
      if (editing) {
        const payload: any = {
          role: values.role,
          display_name: values.display_name || '',
          is_active: values.is_active,
        }
        if (values.password) payload.password = values.password
        await api.patch(`/api/users/${editing.id}`, payload)
        message.success('已保存')
      } else {
        await api.post('/api/users', values)
        message.success('用户已创建')
      }
      setOpen(false)
      load()
    } catch (error) {
      message.error(errorText(error, '保存失败'))
    }
  }

  const remove = async (record: User) => {
    try {
      await api.delete(`/api/users/${record.id}`)
      message.success('已删除')
      load()
    } catch (error) {
      message.error(errorText(error, '删除失败'))
    }
  }

  return (
    <div style={{ padding: 24, overflow: 'auto' }}>
      <Card
        variant="borderless"
        className="page-card"
        title="用户管理"
        extra={
          <Button type="primary" className="btn-gradient" icon={<PlusOutlined />} onClick={openCreate}>
            新建用户
          </Button>
        }
      >
        <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
          管理员可使用全部模型；普通用户仅可使用 HappyHorse 系列模型（文生视频 / 参考图生视频）。
        </Typography.Paragraph>
        <Table
          rowKey="id"
          loading={loading}
          dataSource={users}
          pagination={false}
          columns={[
            { title: '用户名', dataIndex: 'username' },
            { title: '昵称', dataIndex: 'display_name', render: (v: string) => v || '-' },
            {
              title: '角色',
              dataIndex: 'role',
              render: (role: string) => (
                <Tag color={role === 'admin' ? 'blue' : 'default'}>
                  {role === 'admin' ? '管理员（全部模型）' : '普通用户（HappyHorse）'}
                </Tag>
              ),
            },
            {
              title: '状态',
              dataIndex: 'is_active',
              render: (active: boolean) =>
                active ? <Tag color="green">正常</Tag> : <Tag>已停用</Tag>,
            },
            {
              title: '创建时间',
              dataIndex: 'created_at',
              render: (v: string) => dayjs(v).format('YYYY-MM-DD HH:mm'),
            },
            {
              title: '操作',
              render: (_: unknown, record: User) => (
                <Space>
                  <Button size="small" type="link" onClick={() => openEdit(record)}>
                    编辑
                  </Button>
                  {record.id !== me?.id && (
                    <Popconfirm
                      title="删除该用户？"
                      description="该用户的对话记录会一并删除"
                      onConfirm={() => remove(record)}
                    >
                      <Button size="small" type="link" danger>
                        删除
                      </Button>
                    </Popconfirm>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        open={open}
        title={editing ? `编辑用户 - ${editing.username}` : '新建用户'}
        onCancel={() => setOpen(false)}
        onOk={submit}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, min: 2, message: '至少 2 个字符' }]}
          >
            <Input disabled={!!editing} placeholder="登录账号" />
          </Form.Item>
          <Form.Item name="display_name" label="昵称">
            <Input placeholder="选填" />
          </Form.Item>
          <Form.Item
            name="password"
            label={editing ? '重置密码（留空则不修改）' : '密码'}
            rules={editing ? [{ min: 6, message: '至少 6 位' }] : [{ required: true, min: 6 }]}
          >
            <Input.Password placeholder="至少 6 位" autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'user', label: '普通用户 - 仅 HappyHorse 系列' },
                { value: 'admin', label: '管理员 - 全部模型 + 用户管理' },
              ]}
            />
          </Form.Item>
          {editing && (
            <Form.Item name="is_active" label="账号状态" valuePropName="checked">
              <Switch checkedChildren="正常" unCheckedChildren="停用" />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  )
}
