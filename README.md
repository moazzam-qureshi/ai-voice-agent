<div align="center">

# VoiceGen AI

**A customer-support voice agent — RAG-grounded answers, structured lead qualification, branded deliverables. In the browser, in under three minutes.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[Architecture](docs/architecture.md) · [Design system](docs/design.md)

</div>

---

VoiceGen is an open-source voice agent for inbound customer support and
lead qualification. A visitor clicks **Talk to the agent**, a real-time
voice call starts in the browser, and the agent grounds every answer in
indexed business documents via custom RAG. When the call ends, the
visitor downloads a branded PDF summary plus the audio recording, and
the business gets a Discord webhook with the qualified-lead fields.

What makes it different from a typical voice-agent demo:

- **Custom RAG, not the provider's stock KB.** Deepgram emits a
  `FunctionCallRequest` for `search_background`; the browser routes it
  to our FastAPI `/agent/search` endpoint, which runs OpenSearch BM25 +
  kNN hybrid retrieval over a page-level index built by a vision-LLM
  ingestion pipeline (Gemini 2.5 Flash via OpenRouter). The agent quotes
  real content from real documents — no hallucinated capabilities, no
  invented promises.
- **Agent-as-code, no dashboard.** The system prompt, voice, LLM
  provider, and function definitions are all in this repo and ship as
  one `Settings` JSON sent on WebSocket open. Iterating on the agent is
  `git commit && git push` — no external dashboard to drift away from
  the codebase.
- **LLM synthesis on the PDF.** Every transcript turn is persisted to
  Postgres during the call. When the call ends, a worker actor runs a
  `gpt-4o-mini` synthesis pass over the full transcript and writes a
  structured summary (visitor name, project brief, fit assessment with
  reasoning, action items, relevant past work). The PDF is rendered from
  that synthesis — not from whatever lazy arguments the agent passed
  to `wrap_up`.
- **Production-grade guardrails.** Cloudflare Turnstile on call start,
  trusted-proxy IP detection, Redis-backed per-IP rate limits, atomic
  per-day global cost ceiling against Deepgram spend, 24h auto-delete of
  recordings and PDFs, server-minted short-lived Deepgram JWTs so the
  API key never reaches the browser.
- **Reliable wrap-up.** Three layers of defense against the agent
  forgetting to call `wrap_up`: an atomic-wrap-up directive in the system
  prompt, a 15-second client-side watchdog that fires the wrap-up path
  if the agent goes silent without invoking the function, and a 3-minute
  hard time cap. Whichever fires, the worker's LLM synthesis still
  produces a real summary from the persisted transcript.

## Stack

```
Voice loop       Deepgram Voice Agent API (wss://agent.deepgram.com)
                 STT: Flux · TTS: Aura-2 · LLM: gpt-4o-mini via OpenRouter

Frontend         Next.js 16 · React 19 · Tailwind 4 · TypeScript
                 Pulsing-orb UI · Web Audio mixer · MediaRecorder

API              FastAPI · uvicorn · slowapi
                 Deepgram grant-token client · Cloudflare Turnstile

Search           OpenSearch 2.18 (BM25 + kNN, 70/30 hybrid)
                 sentence-transformers all-MiniLM-L6-v2

Synthesis        gpt-4o-mini via OpenRouter (structured JSON output)
                 WeasyPrint (HTML → branded PDF)

Storage          PostgreSQL 16 (alembic) · Redis 7

Queue            Dramatiq · Redis broker · APScheduler

Deploy           Docker Compose · Coolify · auto-deploy on push
```

## Local development

Requires Docker 26+, `uv` for Python, and Node 22+.

```bash
# 1. Copy env template and fill in your keys
cp .env.example .env
# Required: OPENROUTER_API_KEY, DEEPGRAM_API_KEY (Member role or higher)
# Optional: DISCORD_WEBHOOK_URL, TURNSTILE_SECRET/SITEKEY, ADMIN_TOKEN

# 2. Bring up the full stack
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build

# 3. Visit http://localhost:3000
```

For Turnstile in local dev, use the Cloudflare test sitekey
`1x00000000000000000000AA` (always passes). Leave `TURNSTILE_SECRET=""`
to disable verification entirely — the server treats missing secret as
a dev-mode escape hatch.

To upload knowledge documents (the agent searches these during calls):

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
     -F "file=@resume.pdf" -F "tag=resume" \
     http://localhost:8000/admin/knowledge
```

Poll `GET /admin/knowledge/{id}` until `status: indexed`.

## Project layout

```
ai-voice-agent/
├── shared/                       # Cross-service Python packages
│   ├── deepgram/                   Grant-token client + Settings JSON builder
│   ├── indexing/                   VLM page extraction + OpenSearch indexer
│   ├── pdf/                        WeasyPrint template + LLM synthesis
│   ├── discord/                    Plain webhook poster
│   ├── db_models/                  SQLAlchemy models (Call, CallMessage, …)
│   ├── tasks/                      Dramatiq actors (PDF gen, Discord, cleanup, ingest)
│   └── guardrails/                 Rate limit + cost ceiling + Turnstile + proxy
│
├── services/
│   ├── api/                        FastAPI service
│   │   └── src/api/
│   │       ├── main.py             App entry: middleware → limiter → routes
│   │       ├── agent/prompts.py    System prompt + greeting (single source of truth)
│   │       ├── auth/               Per-call Redis session tokens
│   │       ├── routes/             /call/start /agent/* /calls/{id} /artifacts /admin
│   │       └── db/                 Async session + Redis client + OpenSearch store
│   │
│   └── worker/                     Dramatiq worker + APScheduler entry-points
│       └── src/worker/
│           ├── main.py             Worker process target
│           ├── scheduler.py        Hourly cleanup scheduler
│           └── db/                 Sync session for actor bodies
│
├── web/                            Next.js 16 frontend
│   └── src/
│       ├── app/                    Single-page state machine across 5 call states
│       ├── components/             Orb, Transcript, Header
│       └── lib/                    Deepgram WS client, MediaRecorder mixer, API client
│
├── alembic/                        Migrations
├── docs/                           architecture, design
├── docker-compose.yml              Production compose (Coolify)
├── docker-compose.local.yml        Local-dev overlay (exposes host ports)
├── pyproject.toml                  Python deps + uv lock
└── .env.example                    Documented env vars
```

## Production deploy

VoiceGen is wired up for continuous deployment on
[Coolify](https://coolify.io): every push to `main` triggers an
automatic build and rollout. No manual deploy step — push, watch the
Coolify build log, done.

First-time setup:

1. Create the Coolify project pointing at this repo
2. Paste production env vars (a clean version lives at `.env.clean`
   locally, gitignored). Three values are environment-specific and need
   real Coolify domains: `NEXT_PUBLIC_API_BASE_URL`, `PUBLIC_BASE_URL`,
   and (optionally) `DISCORD_WEBHOOK_URL`.
3. In Coolify, set the api service's domain with the `:8000` port suffix
   so Traefik generates routing labels and issues the LE certificate.
4. Add the production web domain to the Turnstile sitekey's allowed
   hostnames in the Cloudflare dashboard (otherwise tokens are rejected).
5. Generate an admin token and upload knowledge documents via
   `curl … /admin/knowledge` once the api domain is live.

The `migrate` service runs `alembic upgrade head` automatically on every
deploy.

## License

MIT. See [LICENSE](LICENSE).

---

<div align="center">

Built by **Moazzam Qureshi** · [GitHub](https://github.com/moazzam-qureshi)

</div>
