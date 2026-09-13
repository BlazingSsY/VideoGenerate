import { lazy, Suspense, useState, useRef } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Video, LogOut, KeyRound, Users as UsersIcon, Sparkles, ChevronDown, Sun, Moon } from 'lucide-react'
import api, { errorText } from './api'
import { useAuth } from './auth'
import Login from './pages/Login'
import AgentDrawer from './components/agent/AgentDrawer'
import { CanvasAgentBridgeContext, type CanvasAgentBinding } from './canvasAgentBridge'
import { cn } from './lib/utils'
import { useTheme } from './theme'

const Canvas = lazy(() => import('./pages/Canvas'))
const Users = lazy(() => import('./pages/Users'))
const Skills = lazy(() => import('./pages/Skills'))

function Shell({ children }: { children: React.ReactNode }) {
  const { user, config, logout } = useAuth()
  const theme = useTheme()
  const navigate = useNavigate()
  const location = useLocation()
  const [pwdOpen, setPwdOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [agentOpen, setAgentOpen] = useState(false)
  const [canvasAgentBinding, setCanvasAgentBinding] = useState<CanvasAgentBinding | null>(null)
  const oldPwdRef = useRef<HTMLInputElement>(null)
  const newPwdRef = useRef<HTMLInputElement>(null)
  const [pwdSaving, setPwdSaving] = useState(false)
  const [pwdError, setPwdError] = useState('')

  const submitPwd = async () => {
    const old_password = oldPwdRef.current?.value || ''
    const new_password = newPwdRef.current?.value || ''
    if (!old_password || new_password.length < 6) { setPwdError('新密码至少 6 位'); return }
    setPwdSaving(true); setPwdError('')
    try {
      await api.post('/api/auth/password', { old_password, new_password })
      setPwdOpen(false)
      oldPwdRef.current!.value = ''; newPwdRef.current!.value = ''
    } catch (error) { setPwdError(errorText(error, '修改失败')) }
    finally { setPwdSaving(false) }
  }

  return (
    <CanvasAgentBridgeContext.Provider value={{ binding: canvasAgentBinding, setBinding: setCanvasAgentBinding }}>
    <div className="flex flex-col h-screen overflow-hidden bg-[var(--color-bg)]">
      {/* Top bar — TapNow style: minimal, dark, icon-driven */}
      <header className="flex items-center justify-between h-12 px-4 border-b border-[var(--color-border)] bg-[var(--color-surface-1)] shrink-0 z-30">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-brand-logo">
            <Video className="w-3.5 h-3.5 text-white" />
          </div>
          <span className="text-sm font-medium text-[var(--color-ink)]">{config?.app_name || '视频生成工作台'}</span>
        </div>

        <nav className="flex items-center gap-0.5">
          <button onClick={() => navigate('/')} className={cn(
            'px-3 py-1.5 text-xs rounded-md transition-colors',
            location.pathname === '/' ? 'text-[var(--color-primary)] bg-[var(--color-surface-3)]' : 'text-[var(--color-ink-secondary)] hover:text-[var(--color-ink)] hover:bg-[var(--color-surface-3)]'
          )}>创意画布</button>
          {user?.role === 'admin' && (<>
            <button onClick={() => navigate('/users')} className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md transition-colors',
              location.pathname === '/users' ? 'text-[var(--color-primary)] bg-[var(--color-surface-3)]' : 'text-[var(--color-ink-secondary)] hover:text-[var(--color-ink)] hover:bg-[var(--color-surface-3)]'
            )}><UsersIcon className="w-3 h-3" /> 用户</button>
            <button onClick={() => navigate('/skills')} className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md transition-colors',
              location.pathname === '/skills' ? 'text-[var(--color-primary)] bg-[var(--color-surface-3)]' : 'text-[var(--color-ink-secondary)] hover:text-[var(--color-ink)] hover:bg-[var(--color-surface-3)]'
            )}><Sparkles className="w-3 h-3" /> 技能</button>
          </>)}
        </nav>

        <div className="relative flex items-center gap-2">
          <button type="button" role="switch" aria-checked={theme.light} aria-label="浅色主题"
            title={theme.light ? '切换到深色' : '切换到浅色'} data-testid="theme-toggle"
            onClick={theme.toggle} className="theme-switch">
            <span className="theme-switch-thumb" />
            <Moon className="theme-switch-moon" aria-hidden="true" />
            <Sun className="theme-switch-sun" aria-hidden="true" />
          </button>
          <button data-testid="account-menu-btn" onClick={() => setMenuOpen(!menuOpen)} className="flex items-center gap-1.5 px-2 py-1 text-xs rounded-md hover:bg-[var(--color-surface-3)] transition-colors">
            <div className="flex items-center justify-center w-6 h-6 rounded-full bg-brand-gradient text-white text-[10px] font-medium">
              {(user?.display_name || user?.username || '?')[0]}
            </div>
            <span className="text-[var(--color-ink)] hidden sm:inline">{user?.display_name || user?.username}</span>
            <ChevronDown className="w-3 h-3 text-[var(--color-ink-tertiary)]" />
          </button>
          {menuOpen && (<>
            <div className="fixed inset-0 z-40" onClick={() => setMenuOpen(false)} />
            <div className="absolute right-0 top-full mt-1 w-44 bg-[var(--color-popover)] border border-[var(--color-border)] rounded-lg shadow-xl py-1 z-50 animate-fade-in">
              <button onClick={() => { setMenuOpen(false); setPwdOpen(true) }} className="flex items-center gap-2 w-full px-3 py-2 text-xs text-[var(--color-ink-secondary)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-ink)] transition-colors">
                <KeyRound className="w-3 h-3" /> 修改密码
              </button>
              <div className="h-px bg-[var(--color-border-soft)] my-1" />
              <button onClick={() => { setMenuOpen(false); logout() }} className="flex items-center gap-2 w-full px-3 py-2 text-xs text-[var(--color-danger)] hover:bg-red-950/50 transition-colors">
                <LogOut className="w-3 h-3" /> 退出登录
              </button>
            </div>
          </>)}
        </div>
      </header>

      <div className="flex-1 flex flex-col min-h-0 relative">{children}</div>

      {/* Agent Drawer — global, anchored to bottom like TapNow */}
      <AgentDrawer
        open={agentOpen}
        onOpenChange={setAgentOpen}
        canvasId={canvasAgentBinding?.canvasId || null}
        onBeforeCanvasWrite={canvasAgentBinding?.save}
        onCanvasChanged={canvasAgentBinding?.reload}
        onRunStatusChanged={canvasAgentBinding?.refreshStatus}
        onTakeOver={canvasAgentBinding?.takeOver}
      />

      {/* Password modal */}
      {pwdOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setPwdOpen(false)}>
          <div className="w-full max-w-sm bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-xl shadow-2xl p-6 animate-fade-in" onClick={e => e.stopPropagation()}>
            <h2 className="text-base font-semibold mb-4 text-[var(--color-ink)]">修改密码</h2>
            {pwdError && <p className="text-xs text-[var(--color-danger)] mb-3">{pwdError}</p>}
            <div className="space-y-3">
              <input ref={oldPwdRef} type="password" placeholder="原密码" className="w-full px-3 py-2 text-sm bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none focus:ring-2 focus:ring-[var(--color-primary)]/30 text-[var(--color-ink)] placeholder:text-[var(--color-ink-tertiary)]" />
              <input ref={newPwdRef} type="password" placeholder="新密码（至少6位）" className="w-full px-3 py-2 text-sm bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none focus:ring-2 focus:ring-[var(--color-primary)]/30 text-[var(--color-ink)] placeholder:text-[var(--color-ink-tertiary)]" />
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setPwdOpen(false)} className="px-4 py-1.5 text-xs text-[var(--color-ink-secondary)] hover:text-[var(--color-ink)] transition-colors">取消</button>
              <button onClick={submitPwd} disabled={pwdSaving} className="px-4 py-1.5 text-xs text-white bg-brand-gradient rounded-lg disabled:opacity-50">{pwdSaving ? '保存中…' : '保存'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
    </CanvasAgentBridgeContext.Provider>
  )
}

export default function App() {
  const { user, loading } = useAuth()
  if (loading) return <div className="flex items-center justify-center h-screen bg-[var(--color-bg)]"><div className="w-8 h-8 border-2 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin" /></div>
  if (!user) return (<Routes><Route path="/login" element={<Login />} /><Route path="*" element={<Navigate to="/login" replace />} /></Routes>)
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Suspense fallback={<div className="flex items-center justify-center h-full"><div className="w-8 h-8 border-2 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin" /></div>}><Canvas /></Suspense>} />
        <Route path="/users" element={user.role === 'admin' ? <Suspense fallback={<div className="flex items-center justify-center h-full"><div className="w-8 h-8 border-2 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin" /></div>}><Users /></Suspense> : <Navigate to="/" replace />} />
        <Route path="/skills" element={user.role === 'admin' ? <Suspense fallback={<div className="flex items-center justify-center h-full"><div className="w-8 h-8 border-2 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin" /></div>}><Skills /></Suspense> : <Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  )
}
