import { useState } from 'react'
import { App as AntApp, Button, Form, Input, Typography } from 'antd'
import { LockOutlined, UserOutlined, VideoCameraOutlined } from '@ant-design/icons'

import api, { errorText } from '../api'
import { useAuth } from '../auth'

const BADGES = ['文生视频', '图生视频', '参考生视频', '自由画布']

export default function Login() {
  const [loading, setLoading] = useState(false)
  const { login } = useAuth()
  const { message } = AntApp.useApp()

  const submit = async (values: { username: string; password: string }) => {
    setLoading(true)
    try {
      const res = await api.post('/api/auth/login', values)
      login(res.data.access_token, res.data.user)
      window.location.href = '/'
    } catch (error) {
      message.error(errorText(error, '登录失败'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-hero">
        <div className="login-hero-inner">
          <div className="login-logo">
            <VideoCameraOutlined />
          </div>
          <h1>视频生成工作台</h1>
          <p>
            汇聚多元视频生成能力，让灵感在自然对话中持续生长，从一瞬构想到一段完整影像。
          </p>
          <div className="login-hero-badges">
            {BADGES.map((badge) => (
              <span key={badge} className="login-hero-badge">
                {badge}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="login-panel">
        <div className="login-card">
          <div style={{ marginBottom: 26 }}>
            <Typography.Title level={4} style={{ margin: '0 0 4px' }}>
              欢迎回来
            </Typography.Title>
            <Typography.Text type="secondary">登录后开始生成视频</Typography.Text>
          </div>
          <Form layout="vertical" onFinish={submit} requiredMark={false}>
            <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
              <Input size="large" prefix={<UserOutlined />} placeholder="用户名" autoFocus />
            </Form.Item>
            <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password size="large" prefix={<LockOutlined />} placeholder="密码" />
            </Form.Item>
            <Button
              type="primary"
              className="btn-gradient"
              size="large"
              htmlType="submit"
              loading={loading}
              block
            >
              登录
            </Button>
          </Form>
        </div>
      </div>
    </div>
  )
}
