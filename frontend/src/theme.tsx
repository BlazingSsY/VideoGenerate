import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useState } from 'react'
import { ConfigProvider, theme as antdTheme } from 'antd'
import zhCN from 'antd/locale/zh_CN'

export type ThemeMode = 'light' | 'dark'

const STORAGE_KEY = 'video-generate-theme'

interface ThemeContextValue {
  mode: ThemeMode
  toggleTheme: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function readTheme(): ThemeMode {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(readTheme)

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = mode
    document.documentElement.style.colorScheme = mode
    try {
      window.localStorage.setItem(STORAGE_KEY, mode)
    } catch {
      // Storage may be unavailable in a restricted browser context; the current session still works.
    }
  }, [mode])

  const toggleTheme = useCallback(() => {
    setMode((current) => (current === 'light' ? 'dark' : 'light'))
  }, [])

  const themeConfig = useMemo(() => {
    const dark = mode === 'dark'
    return {
      algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
      token: dark
        ? {
            colorPrimary: '#b721ff',
            colorLink: '#45d8f5',
            colorInfo: '#21d4fd',
            colorBgBase: '#10111e',
            colorBgContainer: '#171827',
            colorBgElevated: '#1d1d31',
            colorBgLayout: 'transparent',
            colorBorder: '#363650',
            colorBorderSecondary: '#2b2c40',
            colorText: '#f5f2ff',
            colorTextSecondary: '#c0bdd2',
            colorTextTertiary: '#918da7',
            colorTextPlaceholder: '#77738c',
            borderRadius: 12,
            borderRadiusLG: 18,
            borderRadiusSM: 9,
            fontSize: 14,
          }
        : {
            colorPrimary: '#7567f8',
            colorLink: '#5d6fe8',
            colorInfo: '#7567f8',
            colorBgBase: '#f8f9ff',
            colorBgContainer: '#ffffff',
            colorBgElevated: '#ffffff',
            colorBgLayout: 'transparent',
            colorBorder: '#dfe2f1',
            colorBorderSecondary: '#ececf5',
            colorText: '#25243a',
            colorTextSecondary: '#69677e',
            colorTextTertiary: '#8f8da1',
            colorTextPlaceholder: '#aaa8b8',
            borderRadius: 12,
            borderRadiusLG: 18,
            borderRadiusSM: 9,
            fontSize: 14,
          },
      components: dark
        ? {
            Layout: { headerBg: 'transparent', bodyBg: 'transparent', siderBg: 'transparent' },
            Menu: { itemSelectedBg: 'transparent', horizontalItemSelectedColor: '#f5f2ff' },
            Card: { headerBg: 'transparent' },
            Segmented: { itemSelectedBg: '#7e22ce', itemSelectedColor: '#ffffff' },
            Modal: { contentBg: '#191a2c', headerBg: '#191a2c' },
            Drawer: { colorBgElevated: '#171827' },
            Table: { headerBg: '#1d1e32', rowHoverBg: '#22233a' },
          }
        : {
            Layout: { headerBg: 'transparent', bodyBg: 'transparent', siderBg: 'transparent' },
            Menu: { itemSelectedBg: 'transparent', horizontalItemSelectedColor: '#6755df' },
            Card: { headerBg: 'transparent' },
            Segmented: { itemSelectedBg: '#ffffff', itemSelectedColor: '#6755df' },
            Modal: { contentBg: '#ffffff', headerBg: '#ffffff' },
            Drawer: { colorBgElevated: '#fbfaff' },
            Table: { headerBg: '#f7f5ff', rowHoverBg: '#faf9ff' },
          },
    }
  }, [mode])

  const value = useMemo(() => ({ mode, toggleTheme }), [mode, toggleTheme])

  return (
    <ThemeContext.Provider value={value}>
      <ConfigProvider locale={zhCN} theme={themeConfig}>
        {children}
      </ConfigProvider>
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const value = useContext(ThemeContext)
  if (!value) throw new Error('useTheme must be used inside ThemeProvider')
  return value
}
