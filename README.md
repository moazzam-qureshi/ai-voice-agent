# VoiceGen AI

An AI customer-support agent for Moazzam Qureshi's freelancing business.
A visitor lands on the page, clicks **Talk to my AI assistant**, and a
voice call starts in the browser. The agent listens to their project,
searches Moazzam's resume + project documentation via custom RAG, gives
an honest fit assessment, and proposes next steps. The visitor leaves
with a branded PDF summary and the call recording.

**Status:** under construction.

## What makes this different from a vanilla voice-agent demo

The agent uses **custom RAG over Moazzam's own portfolio**, not the voice
provider's built-in knowledge features. Deepgram emits a function call,
the browser routes it to our FastAPI `/agent/search` endpoint, and that
endpoint runs OpenSearch BM25 + kNN hybrid retrieval over an index
of Moazzam's resume and project documentation. The AI-engineering story
is in that retrieval layer, not the voice loop.

## Architecture

- **Voice loop:** [Deepgram Voice Agent API](https://developers.deepgram.com/docs/voice-agent) over WebSocket (STT + LLM + TTS + orchestration in one). Agent definition (system prompt, voice, function defs) lives in our repo, sent as a `Settings` message at connect time.
- **Custom RAG:** FastAPI hybrid-search agent over OpenSearch 2.18 (BM25 + kNN, 70/30 weighting). Same indexing pipeline as DocuAI.
- **VLM page extraction:** Qwen 2.5 VL via OpenRouter (knowledge PDFs are indexed once at upload time)
- **PDF generation:** WeasyPrint (call summary as a branded deliverable)
- **Recording:** captured client-side via `MediaRecorder` mixing visitor mic + agent TTS, posted to our API, served via signed download token
- **Discord:** plain webhook notification on every completed call
- **Persistence:** Postgres 16 (alembic), Redis 7, OpenSearch 2.18
- **Deploy:** Docker Compose on Coolify, auto-deploy on push to main

Full design in [docs/architecture.md](docs/architecture.md) and
[docs/design.md](docs/design.md).

## Local development

```bash
cp .env.example .env
# Fill in OPENROUTER_API_KEY, DEEPGRAM_API_KEY, DISCORD_WEBHOOK_URL.

docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build

# Visit http://localhost:3000
```

For Turnstile in local dev, use the Cloudflare test sitekey
`1x00000000000000000000AA` (always passes). Leave `TURNSTILE_SECRET=""`
to disable verification in dev — the server treats missing secret as a
dev-mode escape hatch.

## Deploying to production

Coolify auto-deploys on every push to `main`. Set all `.env` values in
the Coolify env vars panel (note: `NEXT_PUBLIC_*` are baked at build
time — touching them requires a full web image rebuild).

Built by [Moazzam Qureshi](https://github.com/moazzam-qureshi).
