import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function MarkdownText({ text }: { text: string }) {
  if (!text) return null

  return (
    <div className="my-2 min-w-0 break-words text-sm leading-relaxed text-[var(--color-ink)]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ node: _node, ...props }) => <h1 className="mb-2 mt-4 text-base font-semibold first:mt-0" {...props} />,
          h2: ({ node: _node, ...props }) => <h2 className="mb-2 mt-4 text-[15px] font-semibold first:mt-0" {...props} />,
          h3: ({ node: _node, ...props }) => <h3 className="mb-1.5 mt-3 text-sm font-semibold first:mt-0" {...props} />,
          h4: ({ node: _node, ...props }) => <h4 className="mb-1 mt-3 text-sm font-medium first:mt-0" {...props} />,
          p: ({ node: _node, ...props }) => <p className="my-2 whitespace-pre-wrap first:mt-0 last:mb-0" {...props} />,
          ul: ({ node: _node, ...props }) => <ul className="my-2 list-disc space-y-1 pl-5" {...props} />,
          ol: ({ node: _node, ...props }) => <ol className="my-2 list-decimal space-y-1 pl-5" {...props} />,
          li: ({ node: _node, ...props }) => <li className="pl-0.5 marker:text-[var(--color-ink-tertiary)]" {...props} />,
          strong: ({ node: _node, ...props }) => <strong className="font-semibold text-[var(--color-ink)]" {...props} />,
          blockquote: ({ node: _node, ...props }) => (
            <blockquote className="my-2 border-l-2 border-[var(--color-accent)]/60 pl-3 text-[var(--color-ink-secondary)]" {...props} />
          ),
          pre: ({ node: _node, ...props }) => (
            <pre className="my-2 max-w-full overflow-x-auto rounded-lg border border-[var(--color-border-soft)] bg-[var(--color-surface-1)] p-3 text-xs leading-relaxed" {...props} />
          ),
          code: ({ node: _node, className, ...props }) => (
            <code className={`${className || ''} rounded bg-[var(--color-surface-3)] px-1 py-0.5 font-mono text-[12px] [pre_&]:bg-transparent [pre_&]:p-0`} {...props} />
          ),
          a: ({ node: _node, ...props }) => (
            <a className="text-[var(--color-primary)] underline decoration-current/40 underline-offset-2 hover:decoration-current" target="_blank" rel="noreferrer" {...props} />
          ),
          hr: ({ node: _node, ...props }) => <hr className="my-3 border-[var(--color-border-soft)]" {...props} />,
          table: ({ node: _node, ...props }) => (
            <div className="my-2 max-w-full overflow-x-auto">
              <table className="w-full border-collapse text-xs" {...props} />
            </div>
          ),
          th: ({ node: _node, ...props }) => <th className="border border-[var(--color-border)] bg-[var(--color-surface-2)] px-2 py-1 text-left font-medium" {...props} />,
          td: ({ node: _node, ...props }) => <td className="border border-[var(--color-border)] px-2 py-1 align-top" {...props} />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
