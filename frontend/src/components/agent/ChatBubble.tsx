import { cn } from '../../lib/utils'

export default function ChatBubble({
  role,
  children,
}: {
  role: 'user' | 'assistant'
  children: React.ReactNode
}) {
  return (
    <div className={cn('flex', role === 'user' ? 'justify-end' : 'justify-start')}>
      {role === 'user' ? (
        <div className="min-w-0 max-w-[80%] [overflow-wrap:anywhere] px-3.5 py-2 rounded-2xl rounded-br-md bg-chat-gradient text-white text-sm leading-relaxed">
          {children}
        </div>
      ) : (
        <div className="min-w-0 max-w-[95%] [overflow-wrap:anywhere] text-sm leading-relaxed text-[var(--color-ink)]">
          {children}
        </div>
      )}
    </div>
  )
}
