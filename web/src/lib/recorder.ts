/**
 * Mixed-audio call recorder.
 *
 * Designed to be driven by voice-agent.ts: the session owns the AudioContext
 * (the same one it uses to play the agent's TTS), and hands both the mic
 * stream and a synthetic agent stream (a MediaStreamDestination tap of the
 * agent's playback graph) to startMixedRecording().
 *
 * This eliminates the React-state-lag bug where the recorder used to be
 * constructed before the agent stream existed. With the AudioContext-based
 * approach, both inputs are wired into the recorder's mixer at t=0.
 *
 * Output format depends on the browser:
 *   Chrome/Edge → audio/webm;codecs=opus
 *   Firefox     → audio/webm;codecs=opus
 *   Safari      → audio/mp4
 * We don't transcode — the server stores as-is.
 */

const PREFERRED_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
];

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  for (const t of PREFERRED_MIME_TYPES) {
    if (MediaRecorder.isTypeSupported(t)) return t;
  }
  return undefined;
}

export interface CallRecorderHandle {
  /** Stop the recording and return the final mixed Blob. */
  stop: () => Promise<Blob>;
  /** Stop without waiting for chunks; for error paths. */
  abort: () => void;
}

export interface StartMixedRecordingOptions {
  /** Shared AudioContext — owned by the voice-agent session. The recorder
   *  attaches its mixer node to this context but does NOT close it on stop;
   *  the session is responsible for context lifecycle. */
  audioContext: AudioContext;
  /** Visitor's mic stream (from getUserMedia). */
  micStream: MediaStream;
  /** Agent TTS stream (from a MediaStreamAudioDestinationNode tap of
   *  the agent's playback graph in the same AudioContext). */
  agentStream: MediaStream;
}

/**
 * Start a mixed recording.
 *
 * Mixing approach: build a single MediaStreamAudioDestinationNode inside
 * the supplied AudioContext, connect both input streams (mic + agent) to
 * it, run a MediaRecorder over dest.stream. Browser produces one
 * audio track with both voices.
 */
export function startMixedRecording(
  opts: StartMixedRecordingOptions,
): CallRecorderHandle {
  const { audioContext } = opts;
  const destination = audioContext.createMediaStreamDestination();

  if (opts.micStream.getAudioTracks().length === 0) {
    throw new Error("mic stream has no audio tracks");
  }
  const micSource = audioContext.createMediaStreamSource(opts.micStream);
  micSource.connect(destination);

  if (opts.agentStream.getAudioTracks().length === 0) {
    // This used to be the bug: the agent stream came in track-less because
    // captureStream() on the <audio> tag wasn't producing tracks before
    // playback started. With the AudioContext-routed approach (the agent
    // is sourced from createMediaElementSource → MediaStreamDestination)
    // the track exists from t=0. If we still hit this branch something is
    // wrong upstream — log loudly and continue with mic-only.
    console.warn(
      "[recorder] agent stream has no tracks — recording mic only. This is a bug.",
    );
  } else {
    const agentSource = audioContext.createMediaStreamSource(opts.agentStream);
    agentSource.connect(destination);
  }

  const mimeType = pickMimeType();
  const mr = new MediaRecorder(
    destination.stream,
    mimeType ? { mimeType } : undefined,
  );

  const chunks: Blob[] = [];
  mr.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) chunks.push(e.data);
  };

  // Emit a chunk every 1s — bounds memory and means we have something to
  // upload even on hard disconnect.
  mr.start(1000);

  let stopPromise: Promise<Blob> | null = null;
  const stop = (): Promise<Blob> => {
    if (stopPromise) return stopPromise;
    stopPromise = new Promise<Blob>((resolve) => {
      mr.onstop = () => {
        const blob = new Blob(chunks, { type: mimeType ?? "audio/webm" });
        resolve(blob);
      };
      try {
        if (mr.state !== "inactive") mr.stop();
        else resolve(new Blob(chunks, { type: mimeType ?? "audio/webm" }));
      } catch {
        resolve(new Blob(chunks, { type: mimeType ?? "audio/webm" }));
      }
    });
    return stopPromise;
  };

  const abort = (): void => {
    try {
      if (mr.state !== "inactive") mr.stop();
    } catch {
      /* ignore */
    }
  };

  return { stop, abort };
}

/**
 * @deprecated Kept only for source compatibility during refactor.
 * Use startMixedRecording instead — voice-agent.ts owns the recorder now.
 */
export function startCallRecording(_opts: unknown): CallRecorderHandle {
  throw new Error(
    "startCallRecording is deprecated; the voice-agent session owns the recorder now.",
  );
}

/**
 * @deprecated unused — kept so existing imports don't break.
 */
export function captureStreamFromAudioElement(
  _el: HTMLAudioElement,
): MediaStream | null {
  return null;
}
