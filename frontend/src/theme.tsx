import { createContext, useContext, useEffect, useState } from 'react'

const ThemeContext = createContext({ light: false, toggle: () => {} })

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [light, setLight] = useState(() => {
    try { return localStorage.getItem('vg-theme') === 'light' } catch { return false }
  })
  useEffect(() => {
    document.documentElement.classList.toggle('theme-light', light)
    document.documentElement.style.colorScheme = light ? 'light' : 'dark'
    try { localStorage.setItem('vg-theme', light ? 'light' : 'dark') } catch {}
  }, [light])
  return <ThemeContext.Provider value={{ light, toggle: () => setLight(value => !value) }}>{children}</ThemeContext.Provider>
}

export const useTheme = () => useContext(ThemeContext)
