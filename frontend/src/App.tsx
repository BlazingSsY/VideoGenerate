import { lazy, Suspense, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  App as AntApp,
  Button,
  Dropdown,
  Form,
  Input,
  Layout,
  Menu,
  Modal,
  Spin,
  Tag,
  Tooltip,
} from 'antd'
import {
  ApartmentOutlined,
  LogoutOutlined,
  ExperimentOutlined,
  MoonOutlined,
  SunOutlined,
  UserOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons'

import api, { errorText } from './api'
import { useAuth } from './auth'
import Login from './pages/Login'
import Studio from './pages/Studio'
import { useTheme } from './theme'

const Canvas = lazy(() => import('./pages/Canvas'))
const Users = lazy(() => import('./pages/Users'))
const Skills = lazy(() => import('./pages/Skills'))

const { Header, Content } = Layout

function PasswordModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const { message } = AntApp.useApp()

  const submit = async () => {
    const values = await form.validateFields()
    setSaving(true)
    try {
      await api.post('/api/auth/password', values)
      message.success('密码已更新')
      form.resetFields()
      onClose()
    } catch (error) {
      message.error(errorText(error, '修改失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={open} title="修改密码" onCancel={onClose} onOk={submit} confirmLoading={saving}>
      <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
        <Form.Item name="old_password" label="原密码" rules={[{ required: true }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item
          name="new_password"
          label="新密码"
          rules={[{ required: true, min: 6, message: '至少 6 位' }]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  const { user, config, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const { mode, toggleTheme } = useTheme()
  const [pwdOpen, setPwdOpen] = useState(false)

  const primaryItems = [
    { key: '/', label: '生成工作台', icon: <VideoCameraOutlined /> },
    { key: '/canvas', label: '创意画布', icon: <ApartmentOutlined /> },
  ]

  const accountItems = [
    ...(user?.role === 'admin'
      ? [
          { key: 'users', label: '用户管理', icon: <UserOutlined /> },
          { key: 'skills', label: 'Skills 管理', icon: <ExperimentOutlined /> },
          { type: 'divider' as const },
        ]
      : []),
    { key: 'pwd', label: '修改密码' },
    { key: 'logout', label: '退出登录', icon: <LogoutOutlined />, danger: true },
  ]

  return (
    <Layout className="app-layout">
      <Header className="app-header" style={{ height: 56, lineHeight: '56px' }}>
        <div className="app-brand">
          <span className="brand-mark">
            <VideoCameraOutlined />
          </span>
          <span className="brand-text">{config?.app_name || '视频生成工作台'}</span>
        </div>
        <Menu
          className="app-primary-nav"
          mode="horizontal"
          selectedKeys={[location.pathname]}
          items={primaryItems}
          onClick={(event) => navigate(event.key)}
        />
        <div className="app-header-actions">
          <Tooltip title={mode === 'light' ? '切换至深色模式' : '切换至浅色模式'}>
            <Button
              type="text"
              shape="circle"
              className="theme-toggle"
              icon={mode === 'light' ? <MoonOutlined /> : <SunOutlined />}
              aria-label={mode === 'light' ? '切换至深色模式' : '切换至浅色模式'}
              aria-pressed={mode === 'dark'}
              onClick={toggleTheme}
            />
          </Tooltip>
          <Dropdown
            menu={{
              items: accountItems,
              onClick: ({ key }) => {
                if (key === 'users') navigate('/users')
                else if (key === 'skills') navigate('/skills')
                else if (key === 'pwd') setPwdOpen(true)
                else if (key === 'logout') logout()
              },
            }}
          >
            <Button type="text" className="app-account-button" icon={<UserOutlined />}>
              <span className="app-account-name">{user?.display_name || user?.username}</span>
              <Tag
                className="app-account-role"
                color={user?.role === 'admin' ? 'blue' : 'default'}
                style={{ marginLeft: 8 }}
              >
                {user?.role === 'admin' ? '管理员' : '普通用户'}
              </Tag>
            </Button>
          </Dropdown>
        </div>
      </Header>
      <Content style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>{children}</Content>
      <PasswordModal open={pwdOpen} onClose={() => setPwdOpen(false)} />
    </Layout>
  )
}

export default function App() {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="login-wrap">
        <Spin size="large" />
      </div>
    )
  }

  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Studio />} />
        <Route
          path="/canvas"
          element={
            <Suspense fallback={<div className="canvas-loading"><Spin size="large" /></div>}>
              <Canvas />
            </Suspense>
          }
        />
        <Route
          path="/users"
          element={
            user.role === 'admin' ? (
              <Suspense fallback={<div className="canvas-loading"><Spin size="large" /></div>}>
                <Users />
              </Suspense>
            ) : <Navigate to="/" replace />
          }
        />
        <Route
          path="/skills"
          element={
            user.role === 'admin' ? (
              <Suspense fallback={<div className="canvas-loading"><Spin size="large" /></div>}>
                <Skills />
              </Suspense>
            ) : <Navigate to="/" replace />
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  )
}
