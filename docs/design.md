# VoiceGen AI — Visual Design

**Aesthetic:** Dark-mode terminal. Think Vercel CLI output, Linear's command palette, the GitHub Copilot status bar. Deliberately unlike DocuAI's Notion-warm look (per workspace invariant #6: each of the 9 portfolio products must look like a different product).

---

## Design principles

1. **The call IS the product.** Everything else (landing, downloads) is supporting cast. Visual weight goes to the in-call state.
2. **Audio needs a visual anchor.** Voice apps without visible activity feel broken. The waveform/voice-activity ring is the heartbeat.
3. **Terminal aesthetic, not terminal mimicry.** Use monospace as an *accent*, not as the entire UI. Real terminals have no microphone permission dialogs, no rounded buttons. We borrow the feel, not the literal interface.
4. **One screen, one state at a time.** No multi-pane layouts. The visitor is on the phone — they should be focused.

---

## Color system

```css
:root {
  /* Background scales */
  --bg-base:        #0A0E12;   /* page background, near-black with cyan tint */
  --bg-elevated:    #11161C;   /* cards, call console */
  --bg-subtle:      #161D26;   /* hover, secondary surfaces */

  /* Borders */
  --border:         #1E2832;
  --border-strong:  #2A3744;

  /* Foreground scales */
  --fg:             #E8EEF4;   /* primary text */
  --fg-muted:       #8B98A8;   /* secondary text, labels */
  --fg-faint:       #5A6878;   /* tertiary, captions */
  --fg-dim:         #3B4554;   /* disabled */

  /* Accent — cyan, the only color */
  --accent:         #5CE1E6;   /* primary CTA, voice activity, links */
  --accent-bright:  #82F0F4;   /* hover */
  --accent-deep:    #2DB9BE;   /* pressed, focus ring */
  --accent-glow:    rgba(92, 225, 230, 0.18);   /* shadow / halo */

  /* Status */
  --status-success: #6CE9A6;   /* call connected, ready */
  --status-warning: #F2C265;   /* approaching time cap */
  --status-error:   #F47273;   /* mic denied, call failed */
}
```

There is **one** accent color in this product. Resist the urge to add a second. Status colors are used only for inline icons and badges, never for surfaces or buttons.

---

## Typography

```css
:root {
  --font-mono:  'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, monospace;
  --font-sans:  'Inter', system-ui, -apple-system, sans-serif;
}
```

**Usage rules:**
- **JetBrains Mono** for: the live transcript, status indicators ("LISTENING", "THINKING", "00:42"), button labels, metadata
- **Inter** for: hero copy, paragraph text, the summary PDF body

Type scale:

```
display-lg    44px / 56px       Inter, semibold, -0.02em tracking
display       32px / 40px       Inter, semibold
title         22px / 28px       Inter, medium
body-lg       17px / 26px       Inter, regular
body          15px / 24px       Inter, regular
caption       13px / 20px       JetBrains Mono, regular, +0.02em tracking
mono-label    11px / 16px       JetBrains Mono, medium, uppercase, +0.06em tracking
```

The `mono-label` is the terminal-aesthetic workhorse — it labels everything in the call console.

---

## Layout & spacing

Single column, max-width 720px, centered. No sidebar (no docs to show), no header beyond a 32px-tall slim band with the wordmark.

```
Spacing scale: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96
Border radius: 4 (tight), 8 (cards), 12 (call console), 999 (pill, status badges)
```

---

## Screen states

The whole app is essentially **one page** that progresses through states. No SPA routing.

### State 1 — Idle (pre-call)

```
┌─────────────────────────────────────────────────────────┐
│ VOICEGEN.AI                                  GITHUB →   │  <- 32px header
├─────────────────────────────────────────────────────────┤
│                                                          │
│                                                          │
│             ▶ READY                                      │  <- mono-label, accent
│                                                          │
│             Talk to my AI assistant.                     │  <- display-lg
│             It knows what I've built, what I can do,    │
│             and whether I'm a fit for your project.     │  <- body-lg, fg-muted
│                                                          │
│                                                          │
│         ┌────────────────────────────┐                   │
│         │  ●  START CONVERSATION     │                   │  <- primary CTA, accent fill
│         └────────────────────────────┘                   │
│                                                          │
│             90 second cap · 2 calls per day             │  <- caption, fg-faint
│                                                          │
│                                                          │
│  ─── HOW THIS WORKS ──────────────────────────          │  <- mono-label divider
│                                                          │
│  01  Click start, give mic permission                   │
│  02  Tell the agent about your project                  │  <- mono numbers + body
│  03  It searches my portfolio for relevant work         │
│  04  You leave with a PDF summary and the recording     │
│                                                          │
│                                                          │
│ Built by Moazzam Qureshi · github · upwork              │  <- caption footer
└─────────────────────────────────────────────────────────┘
```

The "▶ READY" + colon-less title pattern echoes a CLI prompt. The CTA has a soft accent-color glow shadow (`box-shadow: 0 0 32px var(--accent-glow)`).

### State 2 — Permission requested

The CTA briefly transitions to a mid-state:

```
         ┌────────────────────────────┐
         │  ◯  REQUESTING MICROPHONE… │
         └────────────────────────────┘
```

The dot becomes a pulsing ring. If the user denies, the button reverts to red `--status-error` and shows: "Mic access denied. Refresh and try again."

### State 3 — In-call

The screen transforms into the "call console":

```
┌─────────────────────────────────────────────────────────┐
│ VOICEGEN.AI                                  GITHUB →   │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ● LIVE   00:42 / 01:30                       END CALL  │  <- mono-label header
│  ──────────────────────────────────────────────────     │
│                                                          │
│                                                          │
│            ╱╲  ╱╲    ╱╲  ╱╲  ╱╲                          │
│           ╱  ╲╱  ╲  ╱  ╲╱  ╲╱  ╲    <-- waveform        │
│          ╱        ╲╱            ╲     animated bars      │
│         ╱                        ╲    cyan, 14 of them   │
│        ╱                          ╲                      │
│                                                          │
│                AGENT IS SPEAKING                         │  <- mono-label, status
│                                                          │
│                                                          │
│  ─── TRANSCRIPT ──────────────────────────────────      │
│                                                          │
│  > AGENT                                                 │
│    Hi! What kind of project are you thinking            │
│    about working with Moazzam on?                       │
│                                                          │
│  > YOU                                                   │
│    I need someone to build a voice agent for...         │  <- live, scrolls bottom
│                                                          │
│  > AGENT                                                 │
│    Got it. Let me check what Moazzam has built          │
│    in that space.  [searching background…]              │  <- tool calls shown inline
│                                                          │
└─────────────────────────────────────────────────────────┘
```

**The waveform** is the visual anchor. 14 vertical bars, cyan, heights animated from the audio level (visitor's input mic level when listening, ElevenLabs' audio output level when agent is speaking — both come from the WebRTC stream as `AudioContext` analyser data). When idle between turns, bars pulse at 8-12% height in a slow sine wave.

**State strip below the waveform** transitions through:
- `LISTENING…` (mic active, agent silent) — accent color
- `THINKING…` (after visitor stops, before agent speaks) — accent color, pulsing
- `SEARCHING BACKGROUND…` (tool call in flight) — accent color, with terminal-style dots
- `AGENT IS SPEAKING` (agent audio active) — accent-bright

**Transcript** auto-scrolls. Each turn is preceded by `> AGENT` or `> YOU` in mono-label style. Tool calls render as inline grey text: `[searching background: "voice agents for support"]`.

**Timer** counts up. At 75 seconds the timer flips to `--status-warning` color. At 90 it forces wrap-up.

### State 4 — Wrap-up (transitional, ~3-5 seconds)

```
┌─────────────────────────────────────────────────────────┐
│  ● ENDING   01:24 / 01:30                                │
│  ──────────────────────────────────────────────────     │
│                                                          │
│           [● ● ●]    GENERATING YOUR PDF…                │  <- animated dots
│                                                          │
│           The agent has what it needs.                   │
│           Compiling your project summary and             │  <- body-lg, fg
│           preparing your recording.                      │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

The waveform fades out, replaced by an animated processing indicator (three cyan dots cycling). This screen exists for 3-5 seconds while the PDF is generated and the recording finalizes.

### State 5 — Downloads ready

```
┌─────────────────────────────────────────────────────────┐
│  ✓ COMPLETE   01:28 total                                │
│  ──────────────────────────────────────────────────     │
│                                                          │
│              ┌─────────┐                                 │
│              │   ✓     │                                 │  <- success icon, accent glow
│              └─────────┘                                 │
│                                                          │
│              Conversation complete.                      │  <- display
│                                                          │
│         Two files are ready. Save them now —             │
│         they're deleted after 24 hours.                  │  <- body-lg, fg-muted
│                                                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │ 📄  SUMMARY                                      │    │
│  │     Project brief, fit assessment, next steps.   │    │  <- card, primary
│  │     PDF · 124 KB                                  │    │
│  │                                          [DOWNLOAD ↓] │
│  └─────────────────────────────────────────────────┘    │
│                                                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │ 🎙  RECORDING                                    │    │
│  │     The full audio of our conversation.          │    │  <- card, secondary
│  │     MP3 · 1.2 MB                                 │    │
│  │                                          [DOWNLOAD ↓] │
│  └─────────────────────────────────────────────────┘    │
│                                                          │
│              [ START A NEW CONVERSATION ]                │  <- ghost button
│                                                          │
└─────────────────────────────────────────────────────────┘
```

**Both downloads are visible simultaneously.** Recording is the second card, fully labeled, not hidden. This is the implicit disclosure pattern we discussed in the huddle — the visitor learns the call was recorded by being offered the recording. No covert behavior.

The "deleted after 24 hours" line is what makes this approach legally clean — visitor knows the data exists AND has a defined lifecycle.

---

## Component patterns

### Primary button (the CTA)

```
height: 52px
padding: 0 32px
background: var(--accent)
color: var(--bg-base)  ← dark text on cyan, high contrast
border-radius: 8px
font: var(--font-mono), 14px, medium, +0.06em tracking
text-transform: uppercase
box-shadow: 0 0 32px var(--accent-glow), inset 0 1px 0 rgba(255,255,255,0.15)

:hover  ↑ scale 1.02, glow doubles
:active ↓ scale 0.98
:focus  outline: 2px solid var(--accent-deep), offset 4px
```

### Ghost button (secondary)

```
background: transparent
border: 1px solid var(--border-strong)
color: var(--fg)
:hover  border: var(--accent), color: var(--accent)
```

### Card

```
background: var(--bg-elevated)
border: 1px solid var(--border)
border-radius: 12px
padding: 20px 24px
:hover  border-color: var(--border-strong)
```

### Mono label (the workhorse)

```html
<span class="mono-label">▶ READY</span>
```

```css
.mono-label {
  font: 11px/16px var(--font-mono);
  font-weight: 500;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--fg-muted);
}
.mono-label.accent { color: var(--accent); }
.mono-label.live::before {
  content: '●';
  color: var(--status-error);
  margin-right: 6px;
  animation: pulse 1s infinite;
}
```

### Status pill

```html
<span class="pill pill-live">LIVE</span>
```

```css
.pill {
  height: 22px;
  padding: 0 10px;
  border-radius: 999px;
  font: 11px var(--font-mono);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.pill-live {
  background: rgba(244, 114, 115, 0.12);
  color: var(--status-error);
}
.pill-thinking {
  background: var(--accent-glow);
  color: var(--accent);
}
```

---

## Waveform implementation notes

The waveform is the design centerpiece — get it right.

**Approach:** Web Audio API `AnalyserNode.getByteFrequencyData()` against either the visitor's mic stream (during LISTENING) or the agent's TTS audio output (during SPEAKING). 14 frequency bins, each maps to one bar.

**Look:**
- 14 bars, 4px wide, 8px gap, centered horizontally
- Min height: 8% of container (60px container → 5px tall)
- Max height: 100% (60px tall)
- Color: `var(--accent)`, rounded top/bottom (1.5px radius)
- Transition: `transform: scaleY(...)` with `transition: transform 80ms ease-out` so bars feel responsive, not laggy or jittery

**Idle pulse** (between turns, no audio):
- Slow sine wave: each bar oscillates between 8-12% height with phase offset between adjacent bars (creates a gentle "wave" rolling across the row)
- 2-second cycle

**Transition between states:** smooth, never instant. When agent stops speaking and turns over to mic input, the waveform doesn't snap — it eases through the idle pulse for ~400ms before picking up the new source.

---

## Summary PDF design

The downloaded artifact. Uses **Inter** for body (PDF readability beats terminal vibe here), but keeps the cyan accent + mono-label pattern.

```
┌──────────────────────────────────────────────────────────┐
│                                                           │
│  VOICEGEN.AI                            [conversation #ID]│  <- mono header
│  ─────────────────                                        │
│                                                           │
│  CONVERSATION SUMMARY                                     │  <- mono-label
│                                                           │
│  Visitor: <visitor_name>                                  │
│  Date: <iso date>                                         │
│  Duration: 01:28                                          │
│  Fit assessment: ● STRONG                                 │  <- with dot, accent
│                                                           │
│  ─────────────────────────────────────────────────       │
│                                                           │
│  THE PROJECT                                              │  <- mono-label, accent
│                                                           │
│  <project_brief — visitor's own words, 2-4 sentences>    │
│                                                           │
│                                                           │
│  WHY MOAZZAM IS A FIT                                     │  <- mono-label, accent
│                                                           │
│  <fit_reasoning, with citations to past projects>        │
│                                                           │
│  Relevant past work:                                      │
│  • DocuAI — Agentic RAG over PDFs with vision-LLM        │
│    extraction. github.com/moazzam-qureshi/...            │
│  • <project 2 if surfaced>                                │
│                                                           │
│                                                           │
│  SUGGESTED NEXT STEPS                                     │  <- mono-label, accent
│                                                           │
│  1. <action_item_1>                                       │
│  2. <action_item_2>                                       │
│  3. <action_item_3>                                       │
│                                                           │
│  ─────────────────────────────────────────────────       │
│                                                           │
│  CONTACT                                                  │  <- mono-label
│                                                           │
│  Moazzam Qureshi                                          │
│  qureshimoazzam7@gmail.com                                │
│  Upwork: <profile url>                                    │
│  GitHub: github.com/moazzam-qureshi                       │
│                                                           │
│                                                           │
│  Generated by VoiceGen AI · github.com/moazzam-qureshi/  │
│  ai-voice-agent · This conversation and recording are    │
│  deleted from VoiceGen servers 24h after generation.     │
│                                                           │
└──────────────────────────────────────────────────────────┘
```

Two-tone print: black body text on white, cyan accent for mono-labels and the fit indicator dot. The PDF should look like a clean consulting deliverable, not a screenshot of the app.

---

## Motion and feedback

- **All state transitions:** 200ms `ease-out`, no longer. Voice apps feel laggy fast; never make the UI add to that perception.
- **Button presses:** 80ms scale-down, scale-up on release. Tactile.
- **Live transcript scrolling:** smooth (CSS `scroll-behavior: smooth`), 300ms.
- **Waveform bars:** see above, 80ms transform transitions.
- **State pill blinking ("● LIVE"):** 1-second sine pulse, opacity 0.6→1.0.
- **No bouncy animations, no spring physics.** This is a terminal aesthetic. Movement should feel precise.

---

## What this design deliberately AVOIDS

- **Multiple competing accent colors.** One cyan. Disciplined.
- **Glassmorphism / blur effects.** Looks like every other AI demo.
- **Gradient buttons.** Solid fills only.
- **Skeumorphic mic icons or phone receivers.** The waveform IS the affordance for "this is a call."
- **Chat-bubble avatars.** The transcript uses `> AGENT` / `> YOU` markers in mono. No round avatars.
- **A "powered by ElevenLabs" badge.** This is portfolio work. Attribution is on the GitHub repo, not the demo UI.

---

## Open visual decisions

1. **Whether the waveform stays during tool calls.** When `search_background` is in flight, the agent isn't speaking but the call isn't truly idle. Options: (a) waveform pulses gently in the idle pattern, (b) waveform morphs into a horizontal "scanning" bar that sweeps left-to-right. Option (b) is more distinctive but requires more design work. Default to (a) for v1.

2. **Mobile layout.** The desktop layout is the demo screenshot target. Mobile should work but doesn't need design polish for the portfolio — most clients will visit on desktop after a portfolio click-through.

3. **The header.** Should it be present at all? Removing it forces full focus on the call console. Keeping it adds a brand anchor. Default: keep, 32px tall, only `VOICEGEN.AI` wordmark + `GITHUB →` link.
