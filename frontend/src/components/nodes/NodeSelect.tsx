import { useViewport } from '@xyflow/react'
import { ChevronDown } from 'lucide-react'
import * as RSelect from '@radix-ui/react-select'

/**
 * 画布内统一圆角下拉框（Radix）。
 * 下拉字号/内边距随画布 zoom 缩放（portal 在 transform 外，需手动匹配）。
 * 全站政策：所有下拉框一律圆角 —— 新增下拉框请复用本组件或 `rounded-lg`+原生 select。
 */
function NodeSelect({ value, onChange, options, placeholder }: {
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
  placeholder?: string
}) {
  const { zoom } = useViewport()
  const z = zoom || 1
  const fs = `${(11 * z).toFixed(1)}px`
  const py = `${(2 * z).toFixed(1)}px`
  const px = `${(8 * z).toFixed(1)}px`
  const gap = `${(4 * z).toFixed(1)}px`

  const selected = options.find(o => o.value === value)
  return (
    <RSelect.Root value={value} onValueChange={onChange}>
      <RSelect.Trigger className="flex items-center justify-between gap-1 w-full px-2 py-1 text-[11px] bg-[var(--color-surface-3)] rounded-lg outline-none border border-transparent hover:border-[var(--color-border)] focus:border-[var(--color-primary)]/30 transition-colors text-[var(--color-ink)]">
        <span className="flex-1 min-w-0 truncate"><RSelect.Value>{selected?.label || placeholder || '选择'}</RSelect.Value></span>
        <ChevronDown className="w-3 h-3 text-[var(--color-ink-tertiary)] shrink-0" />
      </RSelect.Trigger>
      <RSelect.Portal>
        <RSelect.Content
          className="overflow-hidden bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-xl shadow-xl z-[100] animate-fade-in min-w-[100px]"
          position="popper" sideOffset={4 * z}
          style={{ fontSize: fs }}
        >
          <RSelect.Viewport className="p-1" style={{ fontSize: fs, padding: gap }}>
            {options.map(o => (
              <RSelect.Item
                key={o.value} value={o.value}
                className="flex items-center text-[var(--color-ink-secondary)] rounded-lg outline-none cursor-pointer data-[highlighted]:bg-[var(--color-surface-3)] data-[highlighted]:text-[var(--color-ink)] data-[state=checked]:text-[var(--color-primary)] transition-colors"
                style={{ fontSize: fs, padding: `${py} ${px}` }}
              >
                <RSelect.ItemText>{o.label}</RSelect.ItemText>
              </RSelect.Item>
            ))}
          </RSelect.Viewport>
        </RSelect.Content>
      </RSelect.Portal>
    </RSelect.Root>
  )
}

export default NodeSelect
