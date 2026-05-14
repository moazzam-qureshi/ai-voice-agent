"use client";

/**
 * 14-bar waveform driven by Web Audio AnalyserNode.
 *
 * Behavior per design.md:
 *   - When activeStream is null → idle pulse (CSS keyframes)
 *   - When activeStream is set → 14 frequency bins → 14 bars,
 *     bar height = scaleY(magnitude), 80ms transitions
 *   - Color always var(--color-accent)
 *
 * The parent component swaps activeStream as the conversation alternates:
 *   - LISTENING → micStream
 *   - SPEAKING  → agentStream
 *   - THINKING / SEARCHING → null (idle pulse)
 */

import { useEffect, useRef, useState } from "react";

const BAR_COUNT = 14;
const FFT_SIZE = 64; // gives 32 bins; we sample 14 evenly across the lower band

export function Waveform({ activeStream }: { activeStream: MediaStream | null }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const [idle, setIdle] = useState(true);

  useEffect(() => {
    if (!activeStream || activeStream.getAudioTracks().length === 0) {
      teardown();
      setIdle(true);
      return;
    }
    setIdle(false);

    const ctx = new AudioContext();
    audioContextRef.current = ctx;
    const analyser = ctx.createAnalyser();
    analyser.fftSize = FFT_SIZE;
    analyser.smoothingTimeConstant = 0.6;
    analyserRef.current = analyser;

    try {
      const source = ctx.createMediaStreamSource(activeStream);
      source.connect(analyser);
      sourceRef.current = source;
    } catch (e) {
      console.warn("[waveform] createMediaStreamSource failed", e);
      teardown();
      setIdle(true);
      return;
    }

    const data = new Uint8Array(analyser.frequencyBinCount);
    const bars = containerRef.current?.children;
    if (!bars) return;

    const tick = () => {
      analyser.getByteFrequencyData(data);
      // Sample 14 bins evenly across the lower half of the spectrum.
      // Voice energy concentrates in the lower frequencies; sampling
      // the whole range would leave most bars dead.
      for (let i = 0; i < BAR_COUNT; i++) {
        const bin = Math.floor((i / BAR_COUNT) * (data.length / 2));
        const magnitude = data[bin] / 255; // 0..1
        const scale = 0.08 + magnitude * 0.92; // min 0.08, max 1.0
        const el = bars[i] as HTMLDivElement | undefined;
        if (el) el.style.transform = `scaleY(${scale.toFixed(3)})`;
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => teardown();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeStream]);

  function teardown() {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (sourceRef.current) {
      try {
        sourceRef.current.disconnect();
      } catch {
        /* ignore */
      }
      sourceRef.current = null;
    }
    if (audioContextRef.current) {
      try {
        audioContextRef.current.close();
      } catch {
        /* ignore */
      }
      audioContextRef.current = null;
    }
    analyserRef.current = null;
  }

  return (
    <div
      ref={containerRef}
      className={`waveform ${idle ? "waveform--idle" : ""}`}
      aria-hidden="true"
    >
      {Array.from({ length: BAR_COUNT }).map((_, i) => (
        <div key={i} className="waveform__bar" />
      ))}
    </div>
  );
}
