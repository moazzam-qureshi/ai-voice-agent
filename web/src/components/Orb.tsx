"use client";

import { useEffect, useRef, useState } from "react";

/**
 * The Orb — the visual centerpiece of VoiceGen.
 *
 * Renders a glowing sphere whose intensity tracks the active audio
 * stream. The component picks up an AnalyserNode if given a
 * MediaStream and modulates its `--orb-energy` CSS variable in real
 * time, which CSS uses to drive scale + glow.
 *
 * State-driven base looks (color + halo size) come from `state`:
 *   - idle        — soft cyan, slow breathing
 *   - listening   — bright cyan, ripple rings emanating outward
 *   - thinking    — pulsing accent, dimmer halo
 *   - speaking    — brightest bloom, modulated by TTS analyser
 *
 * Audio reactivity is best-effort: if no stream is supplied, the orb
 * still has motion via the idle breathing animation.
 */
export type OrbState = "idle" | "listening" | "thinking" | "speaking";

export interface OrbProps {
  state: OrbState;
  /** Optional MediaStream to drive audio-reactive intensity. */
  audioStream?: MediaStream | null;
  /** Visual size in px (orb diameter, halo extends beyond). */
  size?: number;
}

export function Orb({ state, audioStream, size = 240 }: OrbProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const [energy, setEnergy] = useState(0); // 0..1

  // Set up analyser when a stream arrives.
  useEffect(() => {
    if (!audioStream) {
      analyserRef.current = null;
      return;
    }
    try {
      const ctx = new (window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext })
          .webkitAudioContext)();
      const source = ctx.createMediaStreamSource(audioStream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.75;
      source.connect(analyser);
      analyserRef.current = analyser;
      audioCtxRef.current = ctx;
    } catch {
      analyserRef.current = null;
    }
    return () => {
      analyserRef.current = null;
      const ctx = audioCtxRef.current;
      audioCtxRef.current = null;
      if (ctx && ctx.state !== "closed") void ctx.close();
    };
  }, [audioStream]);

  // Drive the energy value off the analyser.
  useEffect(() => {
    const tick = () => {
      const a = analyserRef.current;
      if (a) {
        const buf = new Uint8Array(a.frequencyBinCount);
        a.getByteFrequencyData(buf);
        // RMS-ish average across low/mid bins where voice lives.
        const slice = buf.slice(0, 48);
        let sum = 0;
        for (let i = 0; i < slice.length; i++) sum += slice[i];
        const avg = sum / slice.length / 255;
        // Soft non-linear curve so quiet speech reads, but loud speech doesn't peak hard.
        setEnergy(Math.min(1, Math.pow(avg, 0.8) * 1.6));
      } else {
        setEnergy(0);
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  return (
    <div
      ref={rootRef}
      className={`orb orb--${state}`}
      style={
        {
          width: size,
          height: size,
          "--orb-energy": energy.toFixed(3),
        } as React.CSSProperties
      }
      aria-hidden
    >
      {/* Outer halo — biggest, blurriest */}
      <div className="orb__halo" />
      {/* Middle glow ring */}
      <div className="orb__ring" />
      {/* Listening: ripple emitter — only renders when state=listening */}
      <div className="orb__ripple orb__ripple--1" />
      <div className="orb__ripple orb__ripple--2" />
      <div className="orb__ripple orb__ripple--3" />
      {/* The orb itself */}
      <div className="orb__core">
        <div className="orb__core-inner" />
      </div>
    </div>
  );
}
