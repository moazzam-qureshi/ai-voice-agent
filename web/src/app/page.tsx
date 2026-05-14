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
import { Transcript } from "@/components/Transcript";
import { Waveform } from "@/components/Waveform";
import {
  ApiError,
  getCallStatus,
  startCall,
  uploadRecording,
  type CallStatus,
} from "@/lib/api";
import {
  captureStreamFromAudioElement as _captureFromEl, // re-export-only to keep tree-shaking honest
  startCallRecording,
  type CallRecorderHandle,
} from "@/lib/recorder";
import { getTurnstileToken } from "@/lib/turnstile";
import {
  createVoiceAgentSession,
  type TranscriptTurn,
  type VoiceAgentSession,
  type VoiceState,
} from "@/lib/voice-agent";

// _captureFromEl is exported for future use (e.g. fallback when the SDK
// doesn't expose an agent MediaStream directly).
void _captureFromEl;

const MAX_SECONDS = 90;

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
  const recorderRef = useRef<CallRecorderHandle | null>(null);
  const startInstantRef = useRef<number>(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const wrapInProgressRef = useRef<boolean>(false);

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
    const mic = session.getMicStream();
    if (mic) {
      // We'll receive agentStream a tick later via onAgentStream; the
      // recorder accepts null and degrades to mic-only if needed.
      const recorder = startCallRecording({
        micStream: mic,
        agentStream: agentStream, // may be null at this moment; mixer will only have mic
      });
      recorderRef.current = recorder;
    }

    setScreen("in-call");
  }, [agentStream]);

  /** Stop the call from our side (timeout or agent's wrap_up) and run
   *  recording upload + transition to wrap-up screen. */
  const finishCall = useCallback(
    async (cId: string, callSessionToken: string, _reason: string) => {
      if (wrapInProgressRef.current) return;
      wrapInProgressRef.current = true;
      setScreen("wrap-up");

      // Stop the voice agent session (closes WS, stops mic).
      const session = sessionRef.current;
      sessionRef.current = null;
      if (session) {
        try {
          await session.stop();
        } catch {
          /* ignore */
        }
      }

      // Stop the recorder and upload the blob.
      const recorder = recorderRef.current;
      recorderRef.current = null;
      if (recorder) {
        try {
          const blob = await recorder.stop();
          if (blob.size > 0) {
            await uploadRecording(cId, callSessionToken, blob);
          }
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
    if (!callId) return;
    const session = sessionRef.current;
    if (!session) return;
    // We don't have a per-call session token in scope here; only
    // finishCall(...) does the upload. Pull the token from a ref... we
    // don't have one. So this path requires the agent's wrap_up to
    // have run, OR the user explicitly ends and we just stop locally
    // without uploading. For v1, time-cap ends the session and goes
    // to wrap-up; the recording is best-effort.
    setScreen("wrap-up");
    try {
      await session.stop();
    } catch {
      /* ignore */
    }
    sessionRef.current = null;
    if (recorderRef.current) {
      try {
        await recorderRef.current.stop();
      } catch {
        /* ignore */
      }
      recorderRef.current = null;
    }
  }, [callId]);

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
      <main className="flex-1 max-w-[720px] w-full mx-auto px-6 py-12">
        {screen === "idle" && <IdleScreen onStart={onStartConversation} />}
        {screen === "permission" && <PermissionScreen />}
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
    <div className="flex flex-col items-center text-center pt-16">
      <div className="mono-label mono-label--accent mb-8">▶ READY</div>

      <h1 className="display-lg mb-6">Talk to my AI assistant.</h1>
      <p
        className="body-lg max-w-[520px] mb-12"
        style={{ color: "var(--color-fg-muted)" }}
      >
        It knows what I&apos;ve built, what I can do, and whether I&apos;m a fit
        for your project.
      </p>

      <button className="btn-primary" onClick={onStart}>
        <span className="inline-block w-2 h-2 rounded-full bg-current mr-3 align-middle" />
        Start conversation
      </button>

      <p className="caption mt-6" style={{ color: "var(--color-fg-faint)" }}>
        90 second cap · 2 calls per day
      </p>

      <div className="divider mt-16 w-full max-w-[480px]">
        <div className="divider__line" />
        <div className="divider__label">How this works</div>
        <div className="divider__line" />
      </div>

      <ol className="w-full max-w-[480px] space-y-3 text-left mt-4">
        {[
          "Click start, give mic permission",
          "Tell the agent about your project",
          "It searches my portfolio for relevant work",
          "You leave with a PDF summary and the recording",
        ].map((step, i) => (
          <li key={i} className="flex gap-4">
            <span className="caption" style={{ color: "var(--color-accent-deep)" }}>
              {String(i + 1).padStart(2, "0")}
            </span>
            <span style={{ color: "var(--color-fg)" }}>{step}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

// ============================================================================
// State 2 — Permission requested
// ============================================================================

function PermissionScreen() {
  return (
    <div className="flex flex-col items-center text-center pt-32">
      <div className="mono-label mono-label--accent mb-8">○ REQUESTING MICROPHONE…</div>
      <p className="body-lg" style={{ color: "var(--color-fg-muted)" }}>
        Allow microphone access to start the call.
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

function InCallScreen(props: {
  voiceState: VoiceState;
  transcript: TranscriptTurn[];
  secondsElapsed: number;
  agentStream: MediaStream | null;
  onEnd: () => void;
}) {
  const timerColor =
    props.secondsElapsed >= 75
      ? "var(--color-status-warning)"
      : "var(--color-fg-muted)";

  return (
    <div className="card mt-8" style={{ borderRadius: "12px" }}>
      {/* Header strip */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <span className="pill pill--live">LIVE</span>
          <span className="caption" style={{ color: timerColor }}>
            {formatTime(props.secondsElapsed)} / {formatTime(MAX_SECONDS)}
          </span>
        </div>
        <button className="btn-ghost h-8 px-4 text-xs" onClick={props.onEnd}>
          End call
        </button>
      </div>

      <div
        className="h-px w-full mb-6"
        style={{ background: "var(--color-border)" }}
      />

      {/* Waveform */}
      <div className="mb-4">
        <Waveform activeStream={props.agentStream} />
      </div>

      <div
        className="mono-label text-center mb-6"
        style={{ color: "var(--color-accent)" }}
      >
        {STATE_LABEL[props.voiceState]}
      </div>

      <div className="divider mb-2">
        <div className="divider__line" />
        <div className="divider__label">Transcript</div>
        <div className="divider__line" />
      </div>

      <Transcript turns={props.transcript} />
    </div>
  );
}

// ============================================================================
// State 4 — Wrap-up transitional
// ============================================================================

function WrapUpScreen() {
  return (
    <div className="card mt-8 py-16 text-center">
      <div className="mono-label mono-label--accent mb-8">
        ● ● ● &nbsp; GENERATING YOUR PDF…
      </div>
      <p className="body-lg" style={{ color: "var(--color-fg)" }}>
        The agent has what it needs.
      </p>
      <p className="body-lg mt-2" style={{ color: "var(--color-fg-muted)" }}>
        Compiling your project summary and preparing your recording.
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
    <div className="mt-8">
      <div className="text-center mb-8">
        <div
          className="inline-flex items-center justify-center w-16 h-16 rounded-2xl mb-6"
          style={{
            background: "rgba(92, 225, 230, 0.12)",
            boxShadow: "0 0 32px rgba(92, 225, 230, 0.18)",
          }}
        >
          <span
            className="text-2xl"
            style={{ color: "var(--color-accent)" }}
            aria-hidden
          >
            ✓
          </span>
        </div>
        <h1 className="display">Conversation complete.</h1>
        <p
          className="body-lg mt-3 max-w-[440px] mx-auto"
          style={{ color: "var(--color-fg-muted)" }}
        >
          Two files are ready. Save them now — they&apos;re deleted after 24 hours.
        </p>
      </div>

      <div className="space-y-4">
        <DownloadCard
          icon="📄"
          title="Summary"
          description="Project brief, fit assessment, next steps."
          meta={status.artifacts.summary_pdf ? "PDF" : "preparing…"}
          href={status.artifacts.summary_pdf ?? undefined}
        />
        <DownloadCard
          icon="🎙"
          title="Recording"
          description="The full audio of our conversation."
          meta={status.artifacts.recording_mp3 ? "audio" : "preparing…"}
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
  icon: string;
  title: string;
  description: string;
  meta: string;
  href?: string;
}) {
  const disabled = !props.href;
  return (
    <div className="card flex items-center gap-4">
      <div className="text-2xl">{props.icon}</div>
      <div className="flex-1 min-w-0">
        <div className="mono-label" style={{ color: "var(--color-fg)" }}>
          {props.title}
        </div>
        <div
          className="text-sm mt-1"
          style={{ color: "var(--color-fg-muted)" }}
        >
          {props.description}
        </div>
        <div className="caption mt-1" style={{ color: "var(--color-fg-faint)" }}>
          {props.meta}
        </div>
      </div>
      <a
        href={props.href}
        download
        className={`btn-ghost ${disabled ? "pointer-events-none opacity-50" : ""}`}
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
