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
  type FitScore,
  type WrapUpInput,
} from "./api";

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
  /** Fired when an audio MediaStream is available for the waveform +
   *  the MediaRecorder mixer. Called once per session. */
  onAgentStream: (stream: MediaStream | null) => void;
}

export interface VoiceAgentSession {
  start: () => Promise<void>;
  stop: () => Promise<void>;
  /** Mic MediaStream for the visitor's side — owned by this session. */
  getMicStream: () => MediaStream | null;
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
  let agentStreamCaptured: MediaStream | null = null;
  let keepAliveInterval: ReturnType<typeof setInterval> | null = null;
  let stopped = false;

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

    // Deepgram wants linear16 PCM 16kHz. We resample using a
    // ScriptProcessorNode for compatibility — AudioWorklet would be
    // cleaner but adds module-loading complexity for marginal latency
    // savings on a 90-second demo.
    // 4096-sample buffer at 16kHz ≈ 256ms per frame.
    micContext = new AudioContext({ sampleRate: 16000 });
    const source = micContext.createMediaStreamSource(micStream);
    const processor = micContext.createScriptProcessor(4096, 1, 1);
    micProcessor = processor;

    processor.onaudioprocess = (e) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const input = e.inputBuffer.getChannelData(0);
      const pcm16 = floatTo16BitPCM(input);
      ws.send(pcm16);
    };

    source.connect(processor);
    processor.connect(micContext.destination);
  }

  // ---- Agent audio playback ----------------------------------------------

  function setupAgentAudio(): void {
    // We use MediaSource to stream mp3 chunks to an <audio> element.
    // captureStream() on that element gives us a MediaStream the
    // recorder + waveform can tap into.
    agentAudioEl = document.createElement("audio");
    agentAudioEl.autoplay = true;
    // playsinline is only typed on HTMLVideoElement, but iOS Safari respects
    // the attribute on <audio> too and refuses inline playback without it.
    agentAudioEl.setAttribute("playsinline", "");
    // Hidden but present in the DOM (Safari needs the element attached
    // to the document for some media operations).
    agentAudioEl.style.display = "none";
    document.body.appendChild(agentAudioEl);

    if (typeof MediaSource === "undefined") {
      callbacks.onError("MediaSource is not supported in this browser");
      return;
    }

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

    // Capture the playback as a MediaStream for the waveform/recorder.
    // Safari's captureStream is gated behind a flag; we degrade gracefully.
    const captureFn =
      (agentAudioEl as HTMLMediaElement & { captureStream?: () => MediaStream })
        .captureStream;
    if (typeof captureFn === "function") {
      try {
        agentStreamCaptured = captureFn.call(agentAudioEl);
        callbacks.onAgentStream(agentStreamCaptured);
      } catch (e) {
        console.warn("[voice-agent] captureStream failed", e);
        callbacks.onAgentStream(null);
      }
    } else {
      callbacks.onAgentStream(null);
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
        callbacks.onState("listening");
        break;
      case "AgentThinking":
        callbacks.onState("thinking");
        break;
      case "AgentStartedSpeaking":
        callbacks.onState("speaking");
        break;
      case "AgentAudioDone":
        callbacks.onState("listening");
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
    cleanupKeepAlive();
    if (ws) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      ws = null;
    }
    if (micProcessor) {
      try {
        micProcessor.disconnect();
      } catch {
        /* ignore */
      }
      micProcessor = null;
    }
    if (micContext) {
      try {
        await micContext.close();
      } catch {
        /* ignore */
      }
      micContext = null;
    }
    if (micStream) {
      micStream.getTracks().forEach((t) => t.stop());
      // Don't null micStream — the recorder may still need it for stop()
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

  return { start, stop, getMicStream };
}

// === Helpers ===============================================================

function floatTo16BitPCM(input: Float32Array): ArrayBuffer {
  const buffer = new ArrayBuffer(input.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

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
