"use client";

/**
 * Conversation transcript — left/right bubble layout (agent left, visitor
 * right), tool calls rendered as centered dashed pills. Auto-scrolls to
 * the latest turn.
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
        className="flex flex-col items-center justify-center h-full"
        style={{ minHeight: "200px" }}
      >
        <div
          className="font-mono mb-2"
          style={{
            fontSize: "10px",
            letterSpacing: "0.12em",
            color: "var(--color-accent)",
            animation: "pulse 1.4s ease-in-out infinite",
          }}
        >
          ● ● ●
        </div>
        <div
          className="font-mono"
          style={{
            fontSize: "11px",
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            color: "var(--color-fg-faint)",
          }}
        >
          Waiting for first turn
        </div>
      </div>
    );
  }

  return (
    <div ref={ref} className="transcript-v2">
      {turns.map((turn) => (
        <div
          key={turn.id}
          className={`transcript-v2__turn transcript-v2__turn--${turn.role}`}
        >
          <div className="transcript-v2__bubble">
            {turn.role !== "tool" && (
              <span className="transcript-v2__role">{ROLE_LABEL[turn.role]}</span>
            )}
            <span>{turn.content}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
