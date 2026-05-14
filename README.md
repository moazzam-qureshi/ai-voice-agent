# VoiceGen AI

An AI customer-support agent for Moazzam Qureshi's freelancing business.
A visitor lands on the page, clicks **Talk to my AI assistant**, and a
voice call starts in the browser. The agent listens to their project,
searches Moazzam's resume + project documentation via custom RAG, gives
an honest fit assessment, and proposes next steps. The visitor leaves
with a branded PDF summary and the call recording.

**Status:** under construction.

## What makes this different from a vanilla ElevenLabs demo

The agent does **not** use ElevenLabs' built-in knowledge base. It calls
a custom `search_background` tool that hits our FastAPI `/agent/search`
endpoint, which runs OpenSearch BM25 + kNN hybrid retrieval over a
purpose-built portfolio index. That's the AI-engineering story this
project is meant to demonstrate.

## Architecture

- **Voice loop:** ElevenLabs Conversational AI (STT + LLM + TTS + WebRTC, all in one)
- **Custom RAG:** FastAPI + LangGraph-style tool agent over OpenSearch 2.18
- **VLM page extraction:** Qwen 2.5 VL via OpenRouter (identical to DocuAI)
- **PDF generation:** WeasyPrint
- **Recording:** ElevenLabs `GET /v1/convai/conversations/{id}/audio`, stored locally, served via signed token
- **Discord:** plain webhook notification on every completed call
- **Persistence:** Postgres 16 (alembic), Redis 7, OpenSearch 2.18
- **Deploy:** Docker Compose on Coolify, auto-deploy on push to main

Full design lives in [docs/architecture.md](docs/architecture.md) and
[docs/design.md](docs/design.md).

## Local development

```bash
cp .env.example .env
# Fill in OPENROUTER_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID,
# DISCORD_WEBHOOK_URL, AGENT_TOOL_SECRET.

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
