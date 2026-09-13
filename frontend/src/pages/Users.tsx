import { useState, useEffect, useRef } from 'react'
import { UserPlus, Trash2, Shield, User as UserIcon } from 'lucide-react'
import api, { errorText } from '../api'
import type { User } from '../types'
import { cn } from '../lib/utils'

export default function Users() {
  const [users, setUsers] = useState<User[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [error, setError] = useState('')
  const usernameRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)
  const roleRef = useRef<HTMLSelectElement>(null)

  const load = async () => { const res = await api.get('/api/users'); setUsers(res.data) }
  useEffect(() => { load() }, [])

  const addUser = async () => {
    const username = usernameRef.current?.value || ''
    const password = passwordRef.current?.value || ''
    const role = roleRef.current?.value || 'user'
    if (!username || password.length < 6) { setError('用户名不能为空，密码至少6位'); return }
    try { await api.post('/api/users', { username, password, role }); setShowAdd(false); setError(''); await load() }
    catch (e) { setError(errorText(e, '创建失败')) }
  }

  const deleteUser = async (id: string) => {
    try { await api.delete(`/api/users/${id}`); await load() } catch {}
  }

  const toggleRole = async (user: User) => {
    try { await api.patch(`/api/users/${user.id}`, { role: user.role === 'admin' ? 'user' : 'admin' }); await load() } catch {}
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 bg-[var(--color-bg)]">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-lg font-semibold text-[var(--color-ink)]">用户管理</h1>
          <button onClick={() => setShowAdd(!showAdd)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-white bg-brand-gradient rounded-lg hover:shadow-md">
            <UserPlus className="w-3.5 h-3.5" /> 新建用户
          </button>
        </div>

        {showAdd && (
          <div className="mb-4 p-4 bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-xl animate-fade-in">
            {error && <p className="text-xs text-[var(--color-danger)] mb-3">{error}</p>}
            <div className="flex gap-2">
              <input ref={usernameRef} placeholder="用户名" className="flex-1 px-3 py-2 text-sm bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none text-[var(--color-ink)] placeholder:text-[var(--color-ink-tertiary)]" />
              <input ref={passwordRef} type="password" placeholder="密码（至少6位）" className="flex-1 px-3 py-2 text-sm bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none text-[var(--color-ink)] placeholder:text-[var(--color-ink-tertiary)]" />
              <select ref={roleRef} className="px-3 py-2 text-sm bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none text-[var(--color-ink)]">
                <option value="user">普通用户</option><option value="admin">管理员</option>
              </select>
              <button onClick={addUser} className="px-4 py-2 text-sm text-white bg-brand-gradient rounded-lg">创建</button>
            </div>
          </div>
        )}

        <div className="space-y-2">
          {users.map(u => (
            <div key={u.id} className="flex items-center gap-3 p-3 bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-lg">
              <div className="flex items-center justify-center w-9 h-9 rounded-full bg-brand-gradient text-white text-sm font-medium">
                {(u.display_name || u.username)[0]}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-[var(--color-ink)] truncate">{u.display_name || u.username}</span>
                  <span className={cn('px-1.5 py-0.5 text-[10px] rounded font-medium', u.role === 'admin' ? 'bg-[var(--color-primary-light)] text-[var(--color-primary)]' : 'bg-[var(--color-surface-3)] text-[var(--color-ink-tertiary)]')}>
                    {u.role === 'admin' ? '管理员' : '用户'}
                  </span>
                </div>
                <span className="text-xs text-[var(--color-ink-tertiary)]">@{u.username}</span>
              </div>
              <button onClick={() => toggleRole(u)} className="p-1.5 rounded-md hover:bg-[var(--color-surface-3)] text-[var(--color-ink-tertiary)] hover:text-[var(--color-ink)]" title="切换角色">
                {u.role === 'admin' ? <Shield className="w-3.5 h-3.5" /> : <UserIcon className="w-3.5 h-3.5" />}
              </button>
              <button onClick={() => deleteUser(u.id)} className="p-1.5 rounded-md hover:bg-red-950/50 text-[var(--color-ink-tertiary)] hover:text-[var(--color-danger)]" title="删除">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
