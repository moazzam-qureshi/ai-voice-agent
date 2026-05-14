/**
 * MediaRecorder that captures BOTH the visitor's mic and the agent's TTS
 * output into a single mixed audio file.
 *
 * Approach (per design.md "Risks" #4):
 *  1. Create an AudioContext and a MediaStreamAudioDestinationNode.
 *  2. Route the mic MediaStream through a MediaStreamAudioSourceNode → dest.
 *  3. Route the agent MediaStream the same way → dest.
 *  4. MediaRecorder against dest.stream captures the mixed audio.
 *
 * Output format depends on the browser:
 *   Chrome/Edge → audio/webm;codecs=opus
 *   Firefox     → audio/webm;codecs=opus
 *   Safari      → audio/mp4
 *
 * We don't transcode — let the browser produce whatever it can, server
 * stores as-is, downloads with the right Content-Type.
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
  /** Stop the recording and return the final Blob. */
  stop: () => Promise<Blob>;
  /** Stop without waiting for chunks; for error paths. */
  abort: () => void;
}

/**
 * Start a mixed recording. Returns a handle that yields the final Blob
 * when stopped.
 *
 * Caller is responsible for:
 *  - Asking permission for the mic via getUserMedia (the mic stream is
 *    passed in here, not requested).
 *  - Providing a MediaStream for the agent's TTS audio (typically derived
 *    from an <audio> element via captureStream() or directly from the
 *    Deepgram SDK).
 *  - Not closing either stream until stop() resolves.
 */
export function startCallRecording(opts: {
  micStream: MediaStream;
  agentStream: MediaStream | null;
}): CallRecorderHandle {
  const audioContext = new AudioContext();
  const destination = audioContext.createMediaStreamDestination();

  const micSource = audioContext.createMediaStreamSource(opts.micStream);
  micSource.connect(destination);

  if (opts.agentStream && opts.agentStream.getAudioTracks().length > 0) {
    const agentSource = audioContext.createMediaStreamSource(opts.agentStream);
    agentSource.connect(destination);
  } else {
    // If we don't have the agent stream (e.g. SDK doesn't expose it), the
    // recording is just the visitor's side. Still useful, just lossy of
    // half the conversation.
    console.warn("[recorder] no agent stream supplied — recording mic only");
  }

  const mimeType = pickMimeType();
  const recorder = new MediaRecorder(
    destination.stream,
    mimeType ? { mimeType } : undefined,
  );

  const chunks: Blob[] = [];
  recorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) chunks.push(e.data);
  };

  recorder.start(1000); // emit a chunk every 1s — bounds memory on long calls

  let stopPromise: Promise<Blob> | null = null;
  const stop = (): Promise<Blob> => {
    if (stopPromise) return stopPromise;
    stopPromise = new Promise<Blob>((resolve) => {
      recorder.onstop = () => {
        const blob = new Blob(chunks, {
          type: mimeType ?? "audio/webm",
        });
        try {
          audioContext.close();
        } catch {
          /* already closed */
        }
        resolve(blob);
      };
      try {
        recorder.stop();
      } catch {
        // already stopped
        resolve(new Blob(chunks, { type: mimeType ?? "audio/webm" }));
      }
    });
    return stopPromise;
  };

  const abort = (): void => {
    try {
      recorder.stop();
    } catch {
      /* ignore */
    }
    try {
      audioContext.close();
    } catch {
      /* ignore */
    }
  };

  return { stop, abort };
}

/**
 * Wrap an HTMLAudioElement so its playback is also captured by an
 * AudioContext destination. Used when the Deepgram SDK plays agent
 * audio via an <audio> tag and we need the same audio to feed both
 * the speakers AND our waveform/recording graph.
 */
export function captureStreamFromAudioElement(el: HTMLAudioElement): MediaStream | null {
  // captureStream is non-standard but widely supported in modern browsers.
  // Safari may need experimental flags; if missing, we skip TTS recording.
  const captureFn =
    (el as HTMLMediaElement & { captureStream?: () => MediaStream }).captureStream;
  if (typeof captureFn !== "function") {
    console.warn("[recorder] HTMLAudioElement.captureStream not supported here");
    return null;
  }
  try {
    return captureFn.call(el);
  } catch (e) {
    console.warn("[recorder] captureStream() failed", e);
    return null;
  }
}
