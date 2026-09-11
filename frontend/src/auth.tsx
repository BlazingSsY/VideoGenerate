import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import api, { TOKEN_KEY } from './api'
import type { AppConfig, User } from './types'

interface AuthState {
  user: User | null
  config: AppConfig | null
  loading: boolean
  login: (token: string, user: User) => void
  logout: () => void
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthState>(null as any)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    if (!localStorage.getItem(TOKEN_KEY)) {
      setUser(null)
      setLoading(false)
      return
    }
    try {
      const [me, cfg] = await Promise.all([api.get('/api/auth/me'), api.get('/api/config')])
      setUser(me.data)
      setConfig(cfg.data)
    } catch {
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const login = useCallback((token: string, nextUser: User) => {
    localStorage.setItem(TOKEN_KEY, token)
    setUser(nextUser)
    api.get('/api/config').then((res) => setConfig(res.data)).catch(() => undefined)
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY)
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, config, loading, login, logout, refresh }),
    [user, config, loading, login, logout, refresh],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  return useContext(AuthContext)
}
