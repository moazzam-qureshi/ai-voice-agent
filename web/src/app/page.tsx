"use client";

/**
 * VoiceGen AI — single-page state machine.
 *
 * Five screen states per design.md:
 *   1. idle         — landing, big CTA, "how this works"
 *   2. permission   — CTA mid-state, requesting mic
 *   3. in-call      — call console with waveform + transcript + timer
 *   4. wrap-up      — transitional "generating PDF" screen (3-5s)
 *   5. downloads    — two download cards side-by-side
 *
 * Errors at any state route to a sibling "error" state with a retry button.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Header } from "@/components/Header";
import { Orb, type OrbState } from "@/components/Orb";
import { Transcript } from "@/components/Transcript";
import {
  agentWrapUp,
  ApiError,
  getCallStatus,
  startCall,
  uploadRecording,
  type CallStatus,
} from "@/lib/api";
import { getTurnstileToken } from "@/lib/turnstile";
import {
  createVoiceAgentSession,
  type TranscriptTurn,
  type VoiceAgentSession,
  type VoiceState,
} from "@/lib/voice-agent";

const MAX_SECONDS = 180;

type Screen = "idle" | "permission" | "in-call" | "wrap-up" | "downloads" | "error";

export default function HomePage() {
  const [screen, setScreen] = useState<Screen>("idle");
  const [errorMsg, setErrorMsg] = useState<string>("");

  // Per-call state
  const [callId, setCallId] = useState<string | null>(null);
  const [voiceState, setVoiceState] = useState<VoiceState>("connecting");
  const [transcript, setTranscript] = useState<TranscriptTurn[]>([]);
  const [secondsElapsed, setSecondsElapsed] = useState(0);
  const [agentStream, setAgentStream] = useState<MediaStream | null>(null);
  const [callStatus, setCallStatus] = useState<CallStatus | null>(null);

  const sessionRef = useRef<VoiceAgentSession | null>(null);
  const startInstantRef = useRef<number>(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Hold the per-call session token + call_id so the time-cap path can
  // still POST /agent/wrap-up and /calls/{id}/recording even when the
  // agent itself didn't invoke wrap_up before the cap fired.
  const callSessionTokenRef = useRef<string | null>(null);
  const callIdRef = useRef<string | null>(null);
  const wrapInProgressRef = useRef<boolean>(false);
  // Forward-reference: the watchdog handler (declared inside
  // onStartConversation's callbacks) needs to call onForceEnd, but
  // onForceEnd is declared later. We install it into this ref once
  // it's defined, and the callback dereferences at firing time.
  const onForceEndRef = useRef<() => Promise<void>>(async () => {});

  // ----- Effect: tick the call timer ---------------------------------------
  useEffect(() => {
    if (screen !== "in-call") return;
    startInstantRef.current = Date.now();
    timerRef.current = setInterval(() => {
      const elapsed = Math.floor((Date.now() - startInstantRef.current) / 1000);
      setSecondsElapsed(elapsed);
      if (elapsed >= MAX_SECONDS && !wrapInProgressRef.current) {
        // Time cap — force wrap-up. We can't force the agent itself, but
        // we can disconnect and proceed to recording upload.
        void onForceEnd();
      }
    }, 250);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screen]);

  // ----- Effect: poll for call status during wrap-up -----------------------
  useEffect(() => {
    if (screen !== "wrap-up" || !callId) return;
    let cancelled = false;
    let consecutiveErrors = 0;
    const poll = async () => {
      try {
        const status = await getCallStatus(callId);
        if (cancelled) return;
        setCallStatus(status);
        if (status.artifacts.summary_pdf && status.artifacts.recording_mp3) {
          setScreen("downloads");
          return;
        }
        consecutiveErrors = 0;
      } catch (e) {
        consecutiveErrors++;
        if (consecutiveErrors >= 5) {
          if (!cancelled) {
            setErrorMsg("Couldn't fetch your call results. Please refresh.");
            setScreen("error");
          }
          return;
        }
      }
      if (!cancelled) setTimeout(poll, 1500);
    };
    void poll();
    return () => {
      cancelled = true;
    };
  }, [screen, callId]);

  // ----- Handlers ----------------------------------------------------------

  const onStartConversation = useCallback(async () => {
    setScreen("permission");
    setTranscript([]);
    setSecondsElapsed(0);
    setCallStatus(null);
    wrapInProgressRef.current = false;

    let turnstileToken = "";
    try {
      turnstileToken = await getTurnstileToken();
    } catch (e) {
      console.error("[page] turnstile failed", e);
      setErrorMsg(e instanceof Error ? e.message : "Couldn't verify you're human.");
      setScreen("error");
      return;
    }

    let callResp;
    try {
      callResp = await startCall(turnstileToken);
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        setErrorMsg("Demo limit reached for today. Try again tomorrow.");
      } else if (e instanceof ApiError && e.status === 503) {
        setErrorMsg("The daily demo budget is exhausted. Try again after midnight UTC.");
      } else {
        setErrorMsg(e instanceof Error ? e.message : "Couldn't start the call.");
      }
      setScreen("error");
      return;
    }

    setCallId(callResp.call_id);
    callIdRef.current = callResp.call_id;
    callSessionTokenRef.current = callResp.call_session_token;

    const session = createVoiceAgentSession({
      deepgramToken: callResp.deepgram_token,
      settingsJson: callResp.settings_json,
      callSessionToken: callResp.call_session_token,
      callbacks: {
        onState: (s) => setVoiceState(s),
        onTranscript: (turn) => setTranscript((prev) => [...prev, turn]),
        onError: (msg) => {
          setErrorMsg(msg);
          setScreen("error");
        },
        onWrapUp: () => {
          // Triggered when /agent/wrap-up acked. Stop recording + upload,
          // then transition to the wrap-up screen.
          void finishCall(callResp.call_id, callResp.call_session_token, "agent-wrap-up");
        },
        onAgentStream: (stream) => setAgentStream(stream),
        onWatchdogWrapUp: () => {
          // Agent went silent without calling wrap_up. Run the same
          // forced-end path the time cap uses — the worker's LLM
          // synthesis layer will still produce a real summary from
          // the persisted transcript.
          console.warn("[page] watchdog wrap-up triggered");
          void onForceEndRef.current();
        },
      },
    });
    sessionRef.current = session;

    try {
      await session.start();
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "Couldn't open the call.");
      setScreen("error");
      return;
    }

    // Mic stream is now owned by session; pull it for the recorder.
    // Note: the recorder is now started inside the voice-agent session
    // itself (see voice-agent.ts setupAgentAudio). This guarantees both
    // mic and agent audio streams are wired into the mixer at t=0,
    // eliminating the prior React-state-lag bug.

    setScreen("in-call");
  }, []);

  /** Stop the call from our side (timeout or agent's wrap_up) and run
   *  recording upload + transition to wrap-up screen. */
  const finishCall = useCallback(
    async (cId: string, callSessionToken: string, _reason: string) => {
      if (wrapInProgressRef.current) return;
      wrapInProgressRef.current = true;
      setScreen("wrap-up");

      const session = sessionRef.current;
      sessionRef.current = null;
      if (!session) return;

      // Order matters: stop the recorder FIRST (which finalizes the blob
      // while the AudioContext is still alive), then stop the session
      // (which closes WS, stops mic, closes AudioContext).
      let blob: Blob | null = null;
      try {
        blob = await session.stopRecording();
      } catch (e) {
        console.error("[page] stopRecording failed", e);
      }
      try {
        await session.stop();
      } catch {
        /* ignore */
      }

      if (blob && blob.size > 0) {
        try {
          await uploadRecording(cId, callSessionToken, blob);
        } catch (e) {
          console.error("[page] recording upload failed", e);
          // Non-fatal: the wrap-up screen still polls /calls/{id};
          // it just won't get a recording artifact.
        }
      }
    },
    [],
  );

  const onForceEnd = useCallback(async () => {
    const cId = callIdRef.current;
    const token = callSessionTokenRef.current;
    if (!cId || !token) return;
    if (wrapInProgressRef.current) return;

    // Build a wrap-up payload from the real transcript so the backend
    // can persist + enqueue PDF generation. The agent itself never
    // called wrap_up (time cap fired first), so we don't have its
    // structured fit assessment — but we do have everything the
    // visitor actually said. The PDF makes it clear the call was
    // truncated; the recording covers the rest.
    const visitorTurns = transcript
      .filter((t) => t.role === "visitor" && t.content.trim().length > 0)
      .map((t) => t.content.trim());
    const projectBrief =
      visitorTurns.length > 0
        ? visitorTurns.join(" ")
        : "(No visitor speech captured before the time cap.)";

    try {
      await agentWrapUp(token, {
        visitor_name: "Caller",
        project_brief: projectBrief.slice(0, 1800),
        fit_score: "partial",
        fit_reasoning:
          "The 3-minute time cap was reached before the agent could give a fit " +
          "assessment. The full conversation is captured in the recording.",
        action_items: [
          "Review the recording for project details",
          "Reach out via email to continue the conversation",
        ],
      });
    } catch (e) {
      console.error("[page] time-cap wrap-up failed", e);
      // Continue to finishCall anyway — recording upload may still succeed.
    }

    await finishCall(cId, token, "time-cap");
  }, [finishCall, transcript]);

  // Make onForceEnd available to the watchdog callback in voice-agent.ts,
  // which is created inside onStartConversation BEFORE onForceEnd has
  // been declared. We install the latest reference on each render; the
  // watchdog dereferences this ref at firing time.
  onForceEndRef.current = onForceEnd;

  const onEndCallClick = useCallback(() => {
    void onForceEnd();
  }, [onForceEnd]);

  const onRetry = useCallback(() => {
    setScreen("idle");
    setErrorMsg("");
    setCallId(null);
    setVoiceState("connecting");
    setTranscript([]);
    setSecondsElapsed(0);
    setAgentStream(null);
    setCallStatus(null);
    wrapInProgressRef.current = false;
  }, []);

  // ----- Render ------------------------------------------------------------

  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1 max-w-[920px] w-full mx-auto px-6 py-8 md:py-12">
        {screen === "idle" && <IdleScreen onStart={onStartConversation} />}
        {screen === "permission" && <PermissionScreen voiceState={voiceState} />}
        {screen === "in-call" && (
          <InCallScreen
            voiceState={voiceState}
            transcript={transcript}
            secondsElapsed={secondsElapsed}
            agentStream={agentStream}
            onEnd={onEndCallClick}
          />
        )}
        {screen === "wrap-up" && <WrapUpScreen />}
        {screen === "downloads" && callStatus && (
          <DownloadsScreen status={callStatus} onRestart={onRetry} />
        )}
        {screen === "error" && <ErrorScreen msg={errorMsg} onRetry={onRetry} />}
      </main>
      <footer className="py-6 text-center caption" style={{ color: "var(--color-fg-faint)" }}>
        Built by{" "}
        <a
          href="https://github.com/moazzam-qureshi"
          className="hover:text-[var(--color-accent)] transition-colors"
        >
          Moazzam Qureshi
        </a>
      </footer>
    </div>
  );
}

// ============================================================================
// State 1 — Idle
// ============================================================================

function IdleScreen({ onStart }: { onStart: () => void }) {
  return (
    <div className="flex flex-col items-center text-center">
      {/* Tagline */}
      <div className="status-indicator mb-10 mt-4">
        <span className="status-indicator__dot" style={{ background: "var(--color-accent)" }} />
        Production AI voice agent · RAG · Deepgram · FastAPI
      </div>

      {/* Hero orb */}
      <div className="mb-10">
        <Orb state="idle" size={220} />
      </div>

      {/* Headline + sub */}
      <h1 className="hero-display mb-6">
        Customer Support Voice Agent
      </h1>
      <p className="hero-sub mb-12">
        A live, RAG-grounded voice agent that qualifies inbound leads,
        answers product questions, and hands off a clean summary to the
        team. Right now it&apos;s deployed as my own intake agent —
        try it, then hire me to build one for your business.
      </p>

      {/* CTA */}
      <button className="btn-primary" onClick={onStart}>
        <span
          className="inline-block w-2 h-2 rounded-full mr-3 align-middle"
          style={{ background: "var(--color-bg-base)" }}
        />
        Talk to the agent
      </button>
      <p className="caption mt-5" style={{ color: "var(--color-fg-faint)" }}>
        Real-time RAG · 3-minute demo cap · PDF + audio recording delivered
      </p>

      {/* How it works — 4 stat cards reframed as capability statements */}
      <div className="mt-20 grid grid-cols-2 md:grid-cols-4 gap-3 w-full max-w-[820px]">
        {[
          {
            n: "01",
            t: "Sub-second voice",
            s: "Deepgram Aura-2 TTS + Flux STT over WebSocket. Real conversational latency, not chatbot-with-mic.",
          },
          {
            n: "02",
            t: "Custom knowledge base",
            s: "Hybrid BM25 + kNN over indexed business docs. Agent quotes real content, no hallucinated promises.",
          },
          {
            n: "03",
            t: "Structured handoff",
            s: "Tool-calling captures qualified-lead fields. LLM synthesizes the PDF from the full transcript.",
          },
          {
            n: "04",
            t: "Production guardrails",
            s: "Turnstile gate, per-IP rate limits, cost ceiling, 24h auto-delete. Coolify-deployed.",
          },
        ].map((step) => (
          <div
            key={step.n}
            className="panel"
            style={{ padding: "20px 18px", textAlign: "left" }}
          >
            <div
              className="font-mono mb-3"
              style={{
                fontSize: "11px",
                letterSpacing: "0.08em",
                color: "var(--color-accent)",
              }}
            >
              {step.n}
            </div>
            <div
              className="mb-1"
              style={{
                font: "500 14px var(--font-sans)",
                color: "var(--color-fg)",
              }}
            >
              {step.t}
            </div>
            <div
              style={{
                font: "400 12px/18px var(--font-sans)",
                color: "var(--color-fg-muted)",
              }}
            >
              {step.s}
            </div>
          </div>
        ))}
      </div>

      {/* Hire-me CTA panel */}
      <div
        className="panel mt-16 w-full max-w-[820px]"
        style={{ padding: "32px 36px" }}
      >
        <div className="grid md:grid-cols-[1.4fr,1fr] gap-8 items-center text-left">
          <div>
            <div
              className="font-mono mb-3"
              style={{
                fontSize: "11px",
                letterSpacing: "0.1em",
                textTransform: "uppercase",
                color: "var(--color-accent)",
              }}
            >
              Want one for your business?
            </div>
            <div
              className="mb-3"
              style={{
                font: "500 22px/30px var(--font-sans)",
                color: "var(--color-fg)",
                letterSpacing: "-0.01em",
              }}
            >
              I build production voice agents for inbound support, sales
              qualification, and customer onboarding.
            </div>
            <div
              style={{
                font: "400 14px/22px var(--font-sans)",
                color: "var(--color-fg-muted)",
              }}
            >
              Custom RAG over your docs, structured CRM handoff, dashboards,
              guardrails, deploy to your stack. Two-week prototype, four-week
              production.
            </div>
          </div>
          <div className="flex flex-col gap-3">
            <a
              href="mailto:qureshimoazzam7@gmail.com?subject=Voice%20agent%20build%20—%20from%20VoiceGen"
              className="btn-primary text-center"
              style={{ display: "inline-flex", alignItems: "center", justifyContent: "center" }}
            >
              Email Moazzam
            </a>
            <a
              href="https://github.com/moazzam-qureshi"
              target="_blank"
              rel="noreferrer"
              className="btn-ghost inline-flex items-center justify-center"
            >
              GitHub portfolio
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// State 2 — Permission requested
// ============================================================================

function PermissionScreen({ voiceState }: { voiceState: VoiceState }) {
  // The "permission" screen is shown for the whole bring-up: getUserMedia,
  // POST /call/start, session.start(). Once the voice-agent session emits
  // "connecting" (after getUserMedia returns) we switch the label so the
  // user knows the slow part isn't mic permission — it's the WS handshake.
  const connecting = voiceState === "connecting";
  const label = connecting ? "Connecting" : "Requesting microphone";
  const sub = connecting
    ? "Setting up your call with the agent. This usually takes a couple of seconds."
    : "Allow microphone access to start the call. Your audio never leaves your machine until you speak.";
  return (
    <div className="flex flex-col items-center text-center pt-12">
      <div className="mb-8">
        <Orb state="thinking" size={160} />
      </div>
      <div className="status-indicator status-indicator--thinking mb-6">
        <span className="status-indicator__dot" />
        {label}
      </div>
      <p className="hero-sub" style={{ fontSize: "16px" }}>
        {sub}
      </p>
    </div>
  );
}

// ============================================================================
// State 3 — In-call console
// ============================================================================

function formatTime(s: number): string {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

const STATE_LABEL: Record<VoiceState, string> = {
  connecting: "CONNECTING…",
  listening: "LISTENING…",
  thinking: "THINKING…",
  searching: "SEARCHING BACKGROUND…",
  speaking: "AGENT IS SPEAKING",
  "wrapping-up": "WRAPPING UP…",
  ended: "CALL ENDED",
  error: "ERROR",
};

function voiceStateToOrb(s: VoiceState): OrbState {
  switch (s) {
    case "speaking":
      return "speaking";
    case "thinking":
    case "searching":
    case "wrapping-up":
      return "thinking";
    case "listening":
      return "listening";
    default:
      return "idle";
  }
}

function InCallScreen(props: {
  voiceState: VoiceState;
  transcript: TranscriptTurn[];
  secondsElapsed: number;
  agentStream: MediaStream | null;
  onEnd: () => void;
}) {
  const warning = props.secondsElapsed >= MAX_SECONDS * 0.75;
  const timerColor = warning
    ? "var(--color-status-warning)"
    : "var(--color-fg-muted)";
  const orbState = voiceStateToOrb(props.voiceState);
  const stateClass =
    orbState === "listening"
      ? "status-indicator--listening"
      : orbState === "thinking"
        ? "status-indicator--thinking"
        : orbState === "speaking"
          ? "status-indicator--speaking"
          : "status-indicator--idle";

  return (
    <div className="grid grid-cols-1 md:grid-cols-[1fr,1.1fr] gap-6 mt-4">
      {/* LEFT — Orb console */}
      <div className="panel flex flex-col items-center" style={{ padding: "36px 32px" }}>
        {/* Status strip */}
        <div className="w-full flex items-center justify-between mb-6">
          <span className="pill pill--live">LIVE</span>
          <span
            className="font-mono text-xs tracking-wider"
            style={{ color: timerColor }}
          >
            {formatTime(props.secondsElapsed)} <span style={{ opacity: 0.4 }}>/</span>{" "}
            {formatTime(MAX_SECONDS)}
          </span>
        </div>

        {/* The orb */}
        <div className="my-6 md:my-10">
          <Orb
            state={orbState}
            audioStream={orbState === "speaking" ? props.agentStream : null}
            size={240}
          />
        </div>

        {/* State indicator */}
        <div className={`status-indicator ${stateClass} mb-8`}>
          <span className="status-indicator__dot" />
          {STATE_LABEL[props.voiceState]}
        </div>

        {/* End-call button */}
        <button className="btn-ghost" onClick={props.onEnd}>
          End call
        </button>
      </div>

      {/* RIGHT — Transcript panel */}
      <div className="panel flex flex-col" style={{ minHeight: "520px" }}>
        <div className="flex items-center justify-between mb-4">
          <div
            className="mono-label"
            style={{ color: "var(--color-fg-muted)" }}
          >
            Live transcript
          </div>
          <div
            className="font-mono"
            style={{
              fontSize: "10px",
              color: "var(--color-fg-faint)",
              letterSpacing: "0.08em",
            }}
          >
            {props.transcript.filter((t) => t.role !== "tool").length} TURNS
          </div>
        </div>
        <div
          className="flex-1 overflow-y-auto pr-2"
          style={{ maxHeight: "560px" }}
        >
          <Transcript turns={props.transcript} />
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// State 4 — Wrap-up transitional
// ============================================================================

function WrapUpScreen() {
  return (
    <div className="flex flex-col items-center text-center mt-8 py-12">
      <div className="mb-8">
        <Orb state="thinking" size={180} />
      </div>
      <div className="status-indicator status-indicator--thinking mb-6">
        <span className="status-indicator__dot" />
        Generating your deliverables
      </div>
      <h1
        className="hero-display mb-3"
        style={{ fontSize: "36px", lineHeight: "44px" }}
      >
        The agent has what it needs.
      </h1>
      <p className="hero-sub" style={{ fontSize: "16px" }}>
        Compiling your project summary and preparing your recording. This
        usually takes 5-10 seconds.
      </p>
    </div>
  );
}

// ============================================================================
// State 5 — Downloads
// ============================================================================

function DownloadsScreen({
  status,
  onRestart,
}: {
  status: CallStatus;
  onRestart: () => void;
}) {
  return (
    <div className="flex flex-col items-center mt-4">
      {/* Success orb */}
      <div className="relative mb-8" style={{ width: 96, height: 96 }}>
        <div
          style={{
            position: "absolute",
            inset: "-30%",
            borderRadius: "50%",
            background:
              "radial-gradient(closest-side, rgba(92,225,230,0.45), rgba(92,225,230,0) 70%)",
            filter: "blur(20px)",
          }}
        />
        <div
          style={{
            position: "relative",
            width: "100%",
            height: "100%",
            borderRadius: "50%",
            background:
              "radial-gradient(circle at 50% 35%, #5CE1E6, #2DB9BE 60%, #0D5C5E)",
            boxShadow:
              "inset 0 0 30px rgba(255,255,255,0.15), 0 0 28px rgba(92,225,230,0.4)",
            display: "grid",
            placeItems: "center",
            color: "var(--color-bg-base)",
          }}
        >
          <svg width="42" height="42" viewBox="0 0 24 24" fill="none">
            <path
              d="M5 12.5l4.5 4.5L19 7.5"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
      </div>

      <div
        className="font-mono mb-3"
        style={{
          fontSize: "11px",
          letterSpacing: "0.12em",
          textTransform: "uppercase",
          color: "var(--color-status-success)",
        }}
      >
        ✓ Conversation complete
      </div>

      <h1
        className="hero-display text-center mb-4"
        style={{ fontSize: "44px", lineHeight: "52px" }}
      >
        Your deliverables are ready.
      </h1>
      <p
        className="hero-sub text-center mb-10"
        style={{ fontSize: "16px" }}
      >
        Two files. Save them now — they&apos;re deleted from VoiceGen servers
        after 24 hours.
      </p>

      <div className="w-full max-w-[640px] space-y-4">
        <DownloadCard
          kind="pdf"
          title="Summary"
          description="Project brief, fit assessment, suggested next steps. Branded PDF."
          meta={status.artifacts.summary_pdf ? "PDF · ready" : "preparing…"}
          href={status.artifacts.summary_pdf ?? undefined}
        />
        <DownloadCard
          kind="audio"
          title="Recording"
          description="The full audio of our conversation, mixed visitor + agent."
          meta={status.artifacts.recording_mp3 ? "Audio · ready" : "preparing…"}
          href={status.artifacts.recording_mp3 ?? undefined}
        />
      </div>

      <div className="text-center mt-12">
        <button className="btn-ghost" onClick={onRestart}>
          Start a new conversation
        </button>
      </div>
    </div>
  );
}

function DownloadCard(props: {
  kind: "pdf" | "audio";
  title: string;
  description: string;
  meta: string;
  href?: string;
}) {
  const disabled = !props.href;
  return (
    <div className="dl-card">
      <div className="dl-card__icon">
        {props.kind === "pdf" ? (
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
            <path
              d="M6 3h9l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinejoin="round"
            />
            <path
              d="M14 3v6h6"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinejoin="round"
            />
            <path
              d="M8 14h8M8 17h5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
        ) : (
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
            <rect x="9" y="3" width="6" height="11" rx="3" stroke="currentColor" strokeWidth="1.5" />
            <path
              d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
        )}
      </div>
      <div className="dl-card__body">
        <div className="dl-card__title">{props.title}</div>
        <div className="dl-card__desc">{props.description}</div>
        <div className="dl-card__meta">{props.meta}</div>
      </div>
      <a
        href={props.href}
        download
        className={`btn-ghost inline-flex items-center justify-center ${disabled ? "pointer-events-none opacity-50" : ""}`}
        style={{ minWidth: "140px", height: "44px" }}
      >
        Download ↓
      </a>
    </div>
  );
}

// ============================================================================
// Error
// ============================================================================

function ErrorScreen({ msg, onRetry }: { msg: string; onRetry: () => void }) {
  return (
    <div className="card mt-16 text-center py-12">
      <div
        className="mono-label mb-4"
        style={{ color: "var(--color-status-error)" }}
      >
        ✕ SOMETHING WENT WRONG
      </div>
      <p
        className="body-lg mb-8 max-w-[440px] mx-auto"
        style={{ color: "var(--color-fg)" }}
      >
        {msg || "An unexpected error occurred."}
      </p>
      <button className="btn-primary" onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}
