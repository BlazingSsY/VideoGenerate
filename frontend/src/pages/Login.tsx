import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Video } from 'lucide-react'
import api, { errorText, TOKEN_KEY } from '../api'
import { useAuth } from '../auth'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const usernameRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    const username = usernameRef.current?.value || ''
    const password = passwordRef.current?.value || ''
    if (!username || !password) { setError('请输入用户名和密码'); return }
    setLoading(true)
    setError('')
    try {
      const res = await api.post('/api/auth/login', { username, password })
      localStorage.setItem(TOKEN_KEY, res.data.access_token)
      login(res.data.access_token, res.data.user)
      navigate('/')
    } catch (err) {
      setError(errorText(err, '登录失败'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-[var(--color-bg)]" style={{minHeight: "100vh"}}>
      <div className="w-full max-w-sm px-6">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-brand-gradient mb-3 shadow-lg">
            <Video className="w-7 h-7 text-white" />
          </div>
          <h1 className="text-xl font-bold text-[var(--color-ink)]">视频生成工作台</h1>
          <p className="text-sm text-[var(--color-ink-tertiary)] mt-1">登录以开始创作</p>
        </div>

        {/* Form */}
        <div className="bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-xl p-6 shadow-2xl">
          {error && (
            <div className="mb-4 px-3 py-2 text-sm text-[var(--color-danger)] bg-red-950/30 rounded-lg">
              {error}
            </div>
          )}
          <div className="space-y-3">
            <div>
              <label className="block text-xs font-medium text-[var(--color-ink-secondary)] mb-1.5">用户名</label>
              <input
                ref={usernameRef}
                type="text"
                autoComplete="username"
                className="w-full px-3 py-2 text-sm bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none focus:ring-2 focus:ring-[var(--color-accent)]/30 focus:border-[var(--color-accent)]"
                onKeyDown={e => { if (e.key === 'Enter') passwordRef.current?.focus() }}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--color-ink-secondary)] mb-1.5">密码</label>
              <input
                ref={passwordRef}
                type="password"
                autoComplete="current-password"
                className="w-full px-3 py-2 text-sm bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none focus:ring-2 focus:ring-[var(--color-accent)]/30 focus:border-[var(--color-accent)]"
                onKeyDown={e => { if (e.key === 'Enter') submit() }}
              />
            </div>
          </div>
          <button
            onClick={submit}
            disabled={loading}
            className="w-full mt-4 py-2.5 text-sm font-medium text-white bg-brand-gradient rounded-lg hover:shadow-md disabled:opacity-50 transition-all"
          >
            {loading ? '登录中…' : '登录'}
          </button>
        </div>
        <p className="text-center text-xs text-[var(--color-ink-tertiary)] mt-4">
          首次部署请使用管理员账号登录
        </p>
      </div>
    </div>
  )
}
