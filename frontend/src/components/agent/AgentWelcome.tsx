import { useState } from 'react'
import { ArrowRight, Bot, Lightbulb, RefreshCw, Sparkles, UserCog } from 'lucide-react'
import { useAuth } from '../../auth'

const SUGGESTIONS = [
  [
    { icon: Lightbulb, label: '下一步', title: '规划下一步创作', description: '从想法到分镜，找到合适的模型与提示词。', prompt: '帮我规划一个视频创作方案' },
    { icon: UserCog, label: '专属技能', title: '创建我的 Agent Skill', description: '整理偏好与流程，定制你的创作方式。', prompt: '帮我创建一个 Agent Skill' },
  ],
  [
    { icon: Sparkles, label: '优化作品', title: '一起看看生成结果', description: '分析失败原因，找到下一次生成的改进方向。', prompt: '帮我分析上一次生成失败的原因' },
    { icon: Bot, label: '模型探索', title: '选一个合适的模型', description: '对比风格、能力与成本，为这次创作做选择。', prompt: '帮我做一次模型横评对比' },
  ],
]

export default function AgentWelcome({ onSelect }: { onSelect: (prompt: string) => void }) {
  const { user } = useAuth()
  const [page, setPage] = useState(0)
  const name = user?.display_name || user?.username

  return (
    <div className="agent-welcome" data-testid="agent-welcome">
      <section className="w-full" aria-labelledby="agent-welcome-heading">
        <div className="flex items-center gap-2 mb-3 text-[var(--color-ink-tertiary)]">
          <Sparkles className="w-5 h-5 shrink-0 text-[var(--color-accent)]" aria-hidden="true" />
          <p className="text-lg tracking-tight truncate" title={name ? `Hi，${name}` : undefined}>
            {name ? `Hi，${name}` : 'Hi，你好'}
          </p>
        </div>
        <h2 id="agent-welcome-heading" className="text-[24px] leading-snug font-medium tracking-tight text-[var(--color-ink)] mb-7">
          今天一起创作点什么？
        </h2>

        <div className="agent-suggestions" aria-live="polite">
          {SUGGESTIONS[page].map(({ icon: Icon, label, title, description, prompt }) => (
            <button key={title} type="button" className="agent-suggestion" onClick={() => onSelect(prompt)}>
              <span className="flex items-center gap-1.5 text-[11px] text-[var(--color-ink-tertiary)]">
                <Icon className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                {label}
                <ArrowRight className="agent-suggestion-arrow w-3.5 h-3.5 ml-auto shrink-0" aria-hidden="true" />
              </span>
              <span className="block mt-3 text-[13px] font-medium leading-relaxed text-[var(--color-ink)]">{title}</span>
              <span className="block mt-1 text-[11px] leading-relaxed text-[var(--color-ink-tertiary)]">{description}</span>
            </button>
          ))}
        </div>

        <div className="flex justify-end mt-4">
          <button type="button" onClick={() => setPage(p => (p + 1) % SUGGESTIONS.length)}
            className="flex items-center gap-1.5 px-1 py-2 text-[11px] text-[var(--color-ink-tertiary)] hover:text-[var(--color-accent)] rounded-md focus-visible:outline-2 focus-visible:outline-[var(--color-accent)] transition-colors"
            aria-label="换一组创作建议">
            <RefreshCw className="w-3 h-3" aria-hidden="true" /> 换一组
          </button>
        </div>
      </section>
    </div>
  )
}
