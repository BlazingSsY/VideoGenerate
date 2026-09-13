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
        <div className="max-w-[80%] px-3.5 py-2 rounded-2xl rounded-br-md bg-chat-gradient text-white text-sm leading-relaxed">
          {children}
        </div>
      ) : (
        <div className="max-w-[90%] text-sm leading-relaxed text-[var(--color-ink)]">
          {children}
        </div>
      )}
    </div>
  )
}
