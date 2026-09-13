export default function MarkdownText({ text }: { text: string }) {
  if (!text) return null
  // Simple rendering: split paragraphs, preserve whitespace
  const paragraphs = text.split('\n\n')
  return (
    <div className="my-2 space-y-2">
      {paragraphs.map((p, i) => (
        <p key={i} className="text-sm leading-relaxed text-[var(--color-ink)] whitespace-pre-wrap">
          {p}
        </p>
      ))}
    </div>
  )
}
