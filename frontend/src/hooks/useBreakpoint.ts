import { useEffect, useState } from 'react'

/** 侧栏折叠成抽屉的临界宽度，和 styles.css 里的断点保持一致 */
export const NARROW_QUERY = '(max-width: 960px)'

export function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(query).matches,
  )

  useEffect(() => {
    const mql = window.matchMedia(query)
    const sync = () => setMatches(mql.matches)

    // change 事件在少数环境（无头浏览器、部分嵌入式 WebView、缩放模拟）不会触发，
    // 漏一次就会把布局卡在错误的模式里，所以再监听一次 resize 兜底。
    sync()
    mql.addEventListener('change', sync)
    window.addEventListener('resize', sync)
    return () => {
      mql.removeEventListener('change', sync)
      window.removeEventListener('resize', sync)
    }
  }, [query])

  return matches
}

/** 窄屏（平板、竖屏、被拖窄的窗口）：侧栏改为抽屉 */
export function useIsNarrow() {
  return useMediaQuery(NARROW_QUERY)
}
