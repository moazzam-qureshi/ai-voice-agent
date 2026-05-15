/**
 * Deepgram Voice Agent client (browser).
 *
 * Opens wss://agent.deepgram.com/v1/agent/converse with a short-lived
 * JWT (minted server-side by /call/start), sends our pre-built Settings
 * JSON, streams mic audio in, plays agent audio out, handles function
 * calls by calling our FastAPI backend and replying over the same WS.
 *
 * Protocol references:
 * - WebSocket URL + auth pattern: deepgram-voice-agent-demo
 *   uses `new WebSocket(url, ["bearer", token])` — the bearer token
 *   rides the Sec-WebSocket-Protocol header (only place browsers
 *   allow a custom header on WS handshake)
 * - JSON message types: docs/architecture.md "Deepgram Voice Agent
 *   configuration" + ToolSearch verification (16 server-sent, 9
 *   client-sent types)
 * - Mic format: linear16 16kHz mono PCM (matches Settings.audio.input)
 * - Agent audio: mp3 chunks streamed as binary WS frames
 */

import {
  agentSearch,
  agentWrapUp,
  appendTranscriptTurn,
  type FitScore,
  type WrapUpInput,
} from "./api";
import { startMixedRecording, type CallRecorderHandle } from "./recorder";

const AGENT_WS_URL = "wss://agent.deepgram.com/v1/agent/converse";

// === Voice state surfaced to the UI ========================================

export type VoiceState =
  | "connecting"
  | "listening"
  | "thinking"
  | "searching"
  | "speaking"
  | "wrapping-up"
  | "ended"
  | "error";

export interface TranscriptTurn {
  id: string;
  role: "agent" | "visitor" | "tool";
  content: string;
}

export interface VoiceAgentCallbacks {
  onState: (state: VoiceState) => void;
  onTranscript: (turn: TranscriptTurn) => void;
  onError: (msg: string) => void;
  /** Fired after wrap_up completes; the UI uses this to start the
   *  recording upload + transition to the downloads screen. */
  onWrapUp: (wrapUp: WrapUpInput) => void;
  /** Fired when an audio MediaStream is available for the waveform.
   *  Called once per session. */
  onAgentStream: (stream: MediaStream | null) => void;
  /** Fired when the watchdog determines the agent has gone silent
   *  without calling wrap_up itself. The page should run its wrap-up
   *  path (post a synthetic /agent/wrap-up, upload the recording,
   *  transition to the wrap-up screen). The synthesis layer in the
   *  worker will still produce a real AI summary from the transcript. */
  onWatchdogWrapUp: () => void;
}

export interface VoiceAgentSession {
  start: () => Promise<void>;
  stop: () => Promise<void>;
  /** Mic MediaStream for the visitor's side — owned by this session. */
  getMicStream: () => MediaStream | null;
  /** Stop the recording and return the mixed-audio Blob. The blob
   *  contains both the visitor's mic and the agent's TTS audio in a
   *  single track, captured deterministically via AudioContext
   *  routing (no React state lag). */
  stopRecording: () => Promise<Blob>;
}

// === Public API ============================================================

export function createVoiceAgentSession(opts: {
  deepgramToken: string;
  settingsJson: Record<string, unknown>;
  callSessionToken: string;
  callbacks: VoiceAgentCallbacks;
}): VoiceAgentSession {
  let ws: WebSocket | null = null;
  let micStream: MediaStream | null = null;
  let micProcessor: ScriptProcessorNode | null = null;
  let micContext: AudioContext | null = null;
  let agentAudioEl: HTMLAudioElement | null = null;
  let agentMediaSource: MediaSource | null = null;
  let agentSourceBuffer: SourceBuffer | null = null;
  const agentMp3Queue: ArrayBuffer[] = [];
  // Web Audio routing for the agent's TTS — feeds both speakers and
  // a MediaStreamDestination (which the recorder taps).
  let agentSourceNode: MediaElementAudioSourceNode | null = null;
  let recorder: CallRecorderHandle | null = null;
  let keepAliveInterval: ReturnType<typeof setInterval> | null = null;
  let stopped = false;
  // Wall-clock at call start, used to compute ts_offset_ms when persisting
  // transcript turns.
  const sessionStartMs = Date.now();

  // Watchdog: if AgentAudioDone fires and neither the user starts speaking
  // nor a wrap_up FunctionCallRequest arrives within WATCHDOG_MS, we
  // assume the agent has wrapped up verbally but forgotten to call the
  // wrap_up function. Fire the page's wrap-up path ourselves.
  const WATCHDOG_MS = 15_000;
  let watchdogTimer: ReturnType<typeof setTimeout> | null = null;
  let wrapUpInFlight = false;
  let watchdogFired = false;

  function armWatchdog(): void {
    if (wrapUpInFlight || watchdogFired || stopped) return;
    clearWatchdog();
    watchdogTimer = setTimeout(() => {
      if (wrapUpInFlight || watchdogFired || stopped) return;
      watchdogFired = true;
      console.warn(
        "[voice-agent] watchdog firing — agent went silent without calling wrap_up",
      );
      try {
        callbacks.onWatchdogWrapUp();
      } catch (e) {
        console.error("[voice-agent] watchdog handler failed", e);
      }
    }, WATCHDOG_MS);
  }

  function clearWatchdog(): void {
    if (watchdogTimer !== null) {
      clearTimeout(watchdogTimer);
      watchdogTimer = null;
    }
  }

  const { callbacks } = opts;

  // ---- Mic setup --------------------------------------------------------

  async function setupMic(): Promise<void> {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    // Permission granted — flip the UI off "REQUESTING MICROPHONE" and
    // onto "CONNECTING" as soon as we have the stream, even though the
    // AudioContext still needs a moment to spin up.
    callbacks.onState("connecting");

    // Use the system's native sample rate (typically 48kHz on Windows/Mac,
    // 44.1kHz on iOS). Forcing 16kHz here triggers Chrome's audio driver
    // resampler init which adds 500-2000ms of "REQUESTING MICROPHONE"
    // hang time. We resample ourselves down to 16kHz inside the
    // ScriptProcessor — that's cheap and runs in real time.
    micContext = new AudioContext();
    const inputSampleRate = micContext.sampleRate;
    const targetSampleRate = 16000;
    const resampleRatio = targetSampleRate / inputSampleRate; // e.g. 16000/48000 = 1/3

    const source = micContext.createMediaStreamSource(micStream);
    // 4096-sample buffer at 48kHz ≈ 85ms; produces ~1365 16kHz samples.
    const processor = micContext.createScriptProcessor(4096, 1, 1);
    micProcessor = processor;

    processor.onaudioprocess = (e) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const input = e.inputBuffer.getChannelData(0);
      // Downsample to 16kHz via simple decimation with averaging.
      // For voice this is indistinguishable from a proper polyphase
      // filter and avoids the cost of one. Chrome's input audio is
      // already low-passed by the AGC/noise-suppression chain above.
      const outLen = Math.floor(input.length * resampleRatio);
      const out = new Int16Array(outLen);
      const step = 1 / resampleRatio;
      let inPos = 0;
      for (let i = 0; i < outLen; i++) {
        const idx = inPos | 0;
        const frac = inPos - idx;
        const a = input[idx] ?? 0;
        const b = input[idx + 1] ?? a;
        const sample = a + (b - a) * frac;
        const clamped = Math.max(-1, Math.min(1, sample));
        out[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
        inPos += step;
      }
      ws.send(out.buffer);
    };

    source.connect(processor);
    processor.connect(micContext.destination);
  }

  // ---- Agent audio playback ----------------------------------------------

  function setupAgentAudio(): void {
    if (!micContext) {
      callbacks.onError("Audio context not initialized");
      return;
    }
    if (!micStream) {
      callbacks.onError("Mic stream not initialized");
      return;
    }
    if (typeof MediaSource === "undefined") {
      callbacks.onError("MediaSource is not supported in this browser");
      return;
    }

    // Stream agent mp3 chunks into a hidden <audio> via MediaSource.
    agentAudioEl = document.createElement("audio");
    agentAudioEl.autoplay = true;
    // playsinline is only typed on HTMLVideoElement, but iOS Safari respects
    // the attribute on <audio> too and refuses inline playback without it.
    agentAudioEl.setAttribute("playsinline", "");
    agentAudioEl.style.display = "none";
    // Critical: muted=false is the default; we want the user to hear the
    // agent through the <audio> element directly. But ALSO route through
    // Web Audio for recording — see below.
    document.body.appendChild(agentAudioEl);

    agentMediaSource = new MediaSource();
    agentAudioEl.src = URL.createObjectURL(agentMediaSource);

    agentMediaSource.addEventListener("sourceopen", () => {
      try {
        agentSourceBuffer = agentMediaSource!.addSourceBuffer("audio/mpeg");
        agentSourceBuffer.addEventListener("updateend", flushAgentQueue);
        flushAgentQueue();
      } catch (e) {
        console.error("[voice-agent] addSourceBuffer failed", e);
        callbacks.onError("Browser cannot play the agent's audio format");
      }
    });

    // Proper agent-audio capture: route through Web Audio so we have a
    // guaranteed MediaStream from the moment setup completes — no
    // dependence on captureStream() (which silently produces a
    // track-less stream until playback starts in some browsers).
    //
    // Architecture:
    //   <audio> element ──┐
    //                     └─→ createMediaElementSource ──┬─→ destination (speakers)
    //                                                    └─→ MediaStreamDestination
    //                                                          .stream (→ recorder)
    //
    // Both the mic and the agent stream land in the same recorder via
    // its internal MediaStreamDestination mixer. The recorder is
    // started here so it has both streams from t=0.

    try {
      agentSourceNode = micContext.createMediaElementSource(agentAudioEl);
    } catch (e) {
      console.error("[voice-agent] createMediaElementSource failed", e);
      callbacks.onError("Could not route the agent's audio for recording");
      return;
    }

    // Connect to speakers so the user hears the agent.
    agentSourceNode.connect(micContext.destination);

    // Tap the same node for the recorder via a MediaStreamDestination.
    const agentDest = micContext.createMediaStreamDestination();
    agentSourceNode.connect(agentDest);
    const agentStreamForRecorder = agentDest.stream;

    // Expose for the waveform / orb energy.
    callbacks.onAgentStream(agentStreamForRecorder);

    // Start the recorder NOW with both streams wired up.
    try {
      recorder = startMixedRecording({
        audioContext: micContext,
        micStream: micStream!,
        agentStream: agentStreamForRecorder,
      });
    } catch (e) {
      console.warn("[voice-agent] recorder failed to start; call audio won't be captured", e);
      recorder = null;
    }
  }

  function flushAgentQueue(): void {
    if (!agentSourceBuffer || agentSourceBuffer.updating) return;
    const next = agentMp3Queue.shift();
    if (next) {
      try {
        agentSourceBuffer.appendBuffer(next);
      } catch (e) {
        console.warn("[voice-agent] appendBuffer failed", e);
      }
    }
  }

  function enqueueAgentAudio(chunk: ArrayBuffer): void {
    agentMp3Queue.push(chunk);
    flushAgentQueue();
  }

  // ---- WebSocket --------------------------------------------------------

  function openWebSocket(): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        // The bearer-token subprotocol pattern: browsers can't set
        // custom headers on WS handshakes, so the protocol field doubles
        // as a side channel for auth.
        ws = new WebSocket(AGENT_WS_URL, ["bearer", opts.deepgramToken]);
        ws.binaryType = "arraybuffer";
      } catch (e) {
        reject(e instanceof Error ? e : new Error(String(e)));
        return;
      }

      ws.onopen = () => {
        callbacks.onState("connecting");
        ws!.send(JSON.stringify(opts.settingsJson));
        // KeepAlive every 8s — Deepgram's connection idle-times around
        // 12s when no audio is flowing (between turns / wrap-up).
        keepAliveInterval = setInterval(() => {
          if (ws?.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "KeepAlive" }));
          }
        }, 8000);
        resolve();
      };

      ws.onmessage = (e) => handleMessage(e);

      ws.onerror = (e) => {
        console.error("[voice-agent] ws error", e);
        callbacks.onError("Connection error");
        callbacks.onState("error");
      };

      ws.onclose = () => {
        cleanupKeepAlive();
        if (!stopped) {
          callbacks.onState("ended");
        }
      };
    });
  }

  function handleMessage(e: MessageEvent): void {
    if (e.data instanceof ArrayBuffer) {
      enqueueAgentAudio(e.data);
      return;
    }

    // JSON control messages
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(e.data as string);
    } catch {
      console.warn("[voice-agent] could not parse message", e.data);
      return;
    }

    const type = msg.type as string;
    switch (type) {
      case "Welcome":
        // Server is ready to receive the Settings (already sent in onopen)
        break;
      case "SettingsApplied":
        callbacks.onState("listening");
        break;
      case "UserStartedSpeaking":
        // Real conversation in progress — kill any pending watchdog.
        clearWatchdog();
        callbacks.onState("listening");
        break;
      case "AgentThinking":
        // Agent is mid-thought; not silent.
        clearWatchdog();
        callbacks.onState("thinking");
        break;
      case "AgentStartedSpeaking":
        clearWatchdog();
        callbacks.onState("speaking");
        break;
      case "AgentAudioDone":
        // Agent finished a turn. If the user doesn't respond and the
        // agent doesn't issue wrap_up within WATCHDOG_MS, we'll
        // assume it's done and force the wrap-up path.
        callbacks.onState("listening");
        armWatchdog();
        break;
      case "ConversationText":
        handleConversationText(msg);
        break;
      case "FunctionCallRequest":
        handleFunctionCallRequest(msg);
        break;
      case "Error":
        console.error("[voice-agent] server Error", msg);
        callbacks.onError(typeof msg.description === "string" ? msg.description : "Agent error");
        break;
      case "Warning":
        console.warn("[voice-agent] server Warning", msg);
        break;
      default:
        // History, PromptUpdated, etc. — fine to ignore for v1
        break;
    }
  }

  function handleConversationText(msg: Record<string, unknown>): void {
    const role = msg.role === "assistant" ? "agent" : "visitor";
    const content = typeof msg.content === "string" ? msg.content : "";
    if (!content) return;
    callbacks.onTranscript({
      id: cryptoRandomId(),
      role,
      content,
    });
    // Persist to DB so the PDF worker has the canonical transcript.
    // Fire-and-forget — failures don't break the call.
    void appendTranscriptTurn(opts.callSessionToken, {
      role,
      content,
      tsOffsetMs: Date.now() - sessionStartMs,
    });
  }

  // ---- Function call routing --------------------------------------------

  async function handleFunctionCallRequest(msg: Record<string, unknown>): Promise<void> {
    const functions = (msg.functions ?? []) as Array<{
      id: string;
      name: string;
      arguments: string;
      client_side: boolean;
    }>;

    for (const fn of functions) {
      let result: unknown;
      let argsObj: Record<string, unknown> = {};
      try {
        argsObj = fn.arguments ? JSON.parse(fn.arguments) : {};
      } catch {
        console.error("[voice-agent] function args not valid JSON", fn.arguments);
      }

      try {
        if (fn.name === "search_background") {
          // Agent is mid-thought; not silent. Kill the watchdog so we
          // don't fire while a search is in flight.
          clearWatchdog();
          callbacks.onState("searching");
          const query = typeof argsObj.query === "string" ? argsObj.query : "";
          const topK = typeof argsObj.top_k === "number" ? argsObj.top_k : 3;
          callbacks.onTranscript({
            id: cryptoRandomId(),
            role: "tool",
            content: `[searching: ${query}]`,
          });
          result = await agentSearch(opts.callSessionToken, query, topK);
        } else if (fn.name === "wrap_up") {
          // Real wrap-up is firing — disable the watchdog entirely.
          wrapUpInFlight = true;
          clearWatchdog();
          callbacks.onState("wrapping-up");
          const wrap: WrapUpInput = {
            visitor_name: stringField(argsObj, "visitor_name"),
            project_brief: stringField(argsObj, "project_brief"),
            fit_score: (stringField(argsObj, "fit_score") || "partial") as FitScore,
            fit_reasoning: stringField(argsObj, "fit_reasoning"),
            action_items: Array.isArray(argsObj.action_items)
              ? (argsObj.action_items as unknown[]).map(String)
              : [],
          };
          result = await agentWrapUp(opts.callSessionToken, wrap);
          // Bubble up to the page so it can start the recording upload.
          callbacks.onWrapUp(wrap);
        } else {
          throw new Error(`unknown function: ${fn.name}`);
        }
      } catch (e) {
        console.error("[voice-agent] function call failed", fn.name, e);
        result = { error: e instanceof Error ? e.message : String(e) };
      }

      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({
            type: "FunctionCallResponse",
            id: fn.id,
            name: fn.name,
            content: JSON.stringify(result),
          }),
        );
      }
    }
  }

  // ---- Lifecycle --------------------------------------------------------

  async function start(): Promise<void> {
    stopped = false;
    callbacks.onState("connecting");
    try {
      await setupMic();
      setupAgentAudio();
      await openWebSocket();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      callbacks.onError(msg);
      callbacks.onState("error");
      throw e;
    }
  }

  async function stop(): Promise<void> {
    stopped = true;
    clearWatchdog();
    cleanupKeepAlive();
    if (ws) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      ws = null;
    }
    // Stop the mic ScriptProcessor so we don't keep sending PCM frames
    // to a closed socket.
    if (micProcessor) {
      try {
        micProcessor.disconnect();
      } catch {
        /* ignore */
      }
      micProcessor = null;
    }
    // Note: we do NOT close micContext here. The recorder is still
    // running off the same AudioContext, and closing it would drop the
    // tail of the recording. The caller is responsible for calling
    // stopRecording() BEFORE stop(), or accepting that the final blob
    // may be missing the last few hundred ms.
    if (agentSourceNode) {
      try {
        agentSourceNode.disconnect();
      } catch {
        /* ignore */
      }
      agentSourceNode = null;
    }
    if (micStream) {
      micStream.getTracks().forEach((t) => t.stop());
      // Don't null micStream — the recorder may still need it.
    }
    if (agentAudioEl) {
      try {
        agentAudioEl.pause();
        agentAudioEl.remove();
      } catch {
        /* ignore */
      }
      agentAudioEl = null;
    }
    // Close AudioContext last, after the recorder has had a chance to
    // finalize. If the caller already invoked stopRecording(), this is
    // safe; if not, the AudioContext will be GC'd eventually.
    if (micContext) {
      try {
        await micContext.close();
      } catch {
        /* ignore */
      }
      micContext = null;
    }
    callbacks.onState("ended");
  }

  function cleanupKeepAlive(): void {
    if (keepAliveInterval) {
      clearInterval(keepAliveInterval);
      keepAliveInterval = null;
    }
  }

  function getMicStream(): MediaStream | null {
    return micStream;
  }

  async function stopRecording(): Promise<Blob> {
    if (!recorder) {
      // Return an empty blob with the recorder's preferred mime; caller
      // can detect zero-size and skip the upload.
      return new Blob([], { type: "audio/webm" });
    }
    const blob = await recorder.stop();
    recorder = null;
    return blob;
  }

  return { start, stop, getMicStream, stopRecording };
}

// === Helpers ===============================================================

function stringField(obj: Record<string, unknown>, key: string): string {
  const v = obj[key];
  return typeof v === "string" ? v : "";
}

function cryptoRandomId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2);
}
