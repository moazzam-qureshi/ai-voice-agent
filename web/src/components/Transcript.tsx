"use client";

/**
 * Auto-scrolling transcript pane. Each turn rendered with `> AGENT` /
 * `> YOU` mono labels per design.md State 3.
 */

import { useEffect, useRef } from "react";
import type { TranscriptTurn } from "@/lib/voice-agent";

const ROLE_LABEL: Record<TranscriptTurn["role"], string> = {
  agent: "AGENT",
  visitor: "YOU",
  tool: "TOOL",
};

export function Transcript({ turns }: { turns: TranscriptTurn[] }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [turns]);

  if (turns.length === 0) {
    return (
      <div
        ref={ref}
        className="mono-label"
        style={{ color: "var(--color-fg-faint)", textAlign: "center", marginTop: "16px" }}
      >
        WAITING FOR FIRST TURN…
      </div>
    );
  }

  return (
    <div
      ref={ref}
      style={{ maxHeight: "320px", overflowY: "auto", paddingRight: "8px" }}
    >
      {turns.map((turn) => (
        <div
          key={turn.id}
          className={`transcript-turn ${turn.role === "tool" ? "transcript-turn--tool" : ""}`}
        >
          <div className="transcript-turn__role">{ROLE_LABEL[turn.role]}</div>
          <div className="transcript-turn__content">{turn.content}</div>
        </div>
      ))}
    </div>
  );
}
