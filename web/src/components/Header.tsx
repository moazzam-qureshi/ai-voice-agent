/**
 * 32px-tall slim band per design.md State 1. Wordmark left, GitHub link
 * right. The only persistent chrome on the page.
 */

export function Header() {
  return (
    <header className="flex items-center justify-between h-8 px-6 border-b border-[var(--color-border)]">
      <div className="mono-label" style={{ color: "var(--color-fg)" }}>
        VOICEGEN.AI
      </div>
      <a
        href="https://github.com/moazzam-qureshi/ai-voice-agent"
        target="_blank"
        rel="noreferrer"
        className="mono-label hover:text-[var(--color-accent)] transition-colors"
      >
        GITHUB →
      </a>
    </header>
  );
}
