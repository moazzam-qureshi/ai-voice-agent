# VoiceGen AI — Architecture

**Product:** VoiceGen AI
**Repo:** `ai-voice-agent/`
**Tagline:** An AI customer support agent for Moazzam's freelancing business
**Status:** Pre-build, architecture locked, scaffolding pending

---

## What it is

A visitor lands on the page, clicks **"Talk to my AI assistant"**, and a voice call starts in the browser. The agent asks what the visitor's project is, then uses RAG over Moazzam's resume and per-project documentation to surface relevant past work, qualify fit, and propose next steps. At call end, the visitor downloads a branded PDF summary plus the audio recording. A Discord webhook fires to Moazzam with the same payload.

**Voice provider: Deepgram Voice Agent API.** The agent is configured entirely via the `Settings` JSON sent on the WebSocket handshake — no dashboard agent to create, no agent ID to track. The whole agent definition (system prompt, voice, LLM, function definitions) lives in our repo at [services/api/src/api/agent/prompts.py](../services/api/src/api/agent/prompts.py).

The portfolio differentiator vs. a stock Deepgram demo: function-calling is wired to **our own RAG endpoints**, not Deepgram's built-in knowledge features. The `search_background` function call is routed to FastAPI `/agent/search`, which runs OpenSearch hybrid retrieval over our portfolio index. That's the AI-engineering story.

> **Migration note (2026-05-14):** This document originally specified ElevenLabs Conversational AI as the voice provider. We swapped to Deepgram because (a) Moazzam has $1,200 of Deepgram credit, (b) the agent-as-code model is cleaner than EL's dashboard-configured agent, (c) function calling is client-side via the WebSocket so we drop the HMAC-verified webhook layer, and (d) audio recording happens client-side via MediaRecorder so we drop the post-call audio fetch actor. The data model and guardrails are unchanged.

---

## System diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                       Browser (Next.js)                              │
│                                                                      │
│  Landing → [Talk to AI] → Turnstile → POST /call/start              │
│                                       (gets Deepgram JWT)            │
│                                       │                              │
│                                       ▼                              │
│                              Deepgram Browser Agent SDK              │
│                              wss://agent.deepgram.com/               │
│                                       │                              │
│                                       │ Settings JSON on open:       │
│                                       │   prompt, listen, think,     │
│                                       │   speak, function_defs       │
│                                       │                              │
│                              ┌─────────────────┐                     │
│                              │ Call UI         │                     │
│                              │  - waveform     │                     │
│                              │  - transcript   │                     │
│                              │  - MediaRecorder│                     │
│                              │    (mixed audio)│                     │
│                              └────────┬────────┘                     │
└───────────────────────────────────────┼──────────────────────────────┘
                                        │
                                        │ When agent emits
                                        │ FunctionCallRequest, browser
                                        │ HTTPs our endpoints, returns
                                        │ FunctionCallResponse over WS:
                                        ▼
┌──────────────────────────────────┐         ┌────────────────────────────┐
│   Deepgram Voice Agent API        │         │   VoiceGen API (FastAPI)   │
│                                   │         │                            │
│  - STT (Deepgram Flux)            │         │  POST /call/start          │
│  - LLM (configurable: OpenAI,     │         │     → Turnstile verify     │
│    Anthropic, Google, Bedrock,    │         │     → rate/cost check      │
│    Groq)                          │         │     → mint Deepgram JWT    │
│  - TTS (Deepgram Aura-2 by        │         │       via grant-token      │
│    default; ElevenLabs/Cartesia   │         │     → create Call row      │
│    /OpenAI/Polly also supported)  │         │     → return {call_id,     │
│                                   │         │        deepgram_token,     │
│  Functions the agent invokes      │         │        settings_json}      │
│  (client_side: true — browser     │         │                            │
│  is the orchestrator):            │         │  POST /agent/search        │
│   • search_background(query)      │  ◄────  │   (browser bearer-auth)    │
│   • wrap_up(project_brief, fit,   │         │   → embed + hybrid search  │
│     action_items)                 │         │   → top-k passages         │
│                                   │         │                            │
│                                   │         │  POST /agent/wrap-up       │
│                                   │  ◄────  │   (browser bearer-auth)    │
│                                   │         │   → update Call row        │
│                                   │         │   → enqueue PDF gen        │
│                                   │         │                            │
│                                   │         │  POST /calls/{id}/recording│
│                                   │  ◄────  │   (multipart upload from   │
│                                   │         │    browser MediaRecorder)  │
│                                   │         │   → store mp3, mint        │
│                                   │         │     download_token         │
│                                   │         │   → enqueue Discord notify │
└──────────────────────────────────┘         └────────────┬───────────────┘
                                                          │
                          ┌───────────────────────────────┼─────────────┐
                          │                               │             │
                          ▼                               ▼             ▼
                  ┌──────────────┐               ┌──────────────┐  ┌─────────┐
                  │ OpenSearch   │               │ Postgres     │  │ Redis   │
                  │              │               │              │  │         │
                  │ Indexed:     │               │ calls table  │  │ rate    │
                  │ resume.pdf,  │               │ messages     │  │ limit + │
                  │ project-*.pdf│               │ artifacts    │  │ cost    │
                  └──────────────┘               └──────────────┘  │ counter │
                                                                    └─────────┘
                  ┌──────────────────────────────────────────────┐
                  │  Worker (Dramatiq)                            │
                  │   - PDF ingest (DocuAI-style VLM extraction) │
                  │   - PDF summary generation (call end)         │
                  │   - Discord webhook notify                    │
                  │   - 24h auto-cleanup of recordings + PDFs    │
                  └──────────────────────────────────────────────┘
```

---

## Tech stack

Reused from DocuAI (don't rebuild what works):

- **Backend:** FastAPI + Python 3.13, uv-managed deps
- **Agent layer (for the search tool):** LangGraph with `search` and `synthesize` tools
- **Search index:** OpenSearch 2.18 with BM25 + kNN hybrid (70/30 weight)
- **Embeddings:** sentence-transformers `all-MiniLM-L6-v2`, CPU-only
- **VLM page extraction:** Qwen 2.5 VL via OpenRouter, identical to DocuAI's ingest
- **Persistence:** Postgres 16 (alembic), Redis 7
- **Queue:** Dramatiq with Redis broker, APScheduler for cleanup
- **Frontend:** Next.js 16, React 19, Tailwind 4, TypeScript
- **Deploy:** Docker Compose on Coolify, auto-deploy on push to main

New for this project:

- **Voice loop:** [Deepgram Voice Agent API](https://developers.deepgram.com/docs/voice-agent) over WebSocket at `wss://agent.deepgram.com/`. Bundles STT (Deepgram Flux), LLM (OpenAI gpt-4o-mini by default, but the Settings message supports Anthropic/Google/Bedrock/Groq), and TTS (Deepgram Aura-2) into a single duplex stream.
- **Browser SDK:** [`@deepgram/sdk`](https://github.com/deepgram/deepgram-js-sdk) (their Browser Agent SDK). Connects with a server-minted short-lived JWT — API key never reaches the client.
- **Recording:** captured client-side via `MediaRecorder` mixing the visitor's mic stream and Deepgram's TTS audio stream. Posted to FastAPI as multipart at call end; stored at `/data/calls/<call_id>/recording.mp3`. We don't pull audio from Deepgram post-call.
- **PDF generation:** WeasyPrint (HTML+CSS → PDF, brand-controllable, works headless in Docker)
- **Discord:** plain HTTPS webhook, no SDK

Explicitly not used: Deepgram's built-in KB features. The agent's knowledge lookups go to our `/agent/search` endpoint via Deepgram function-calling with `client_side: true` (the browser orchestrates).

---

## Data model

### Tables

```sql
-- Each call session
CREATE TABLE calls (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_ip INET NOT NULL,
  elevenlabs_conversation_id TEXT,        -- correlation key with EL
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at TIMESTAMPTZ,
  duration_seconds INT,
  status TEXT NOT NULL DEFAULT 'in_progress', -- in_progress | completed | failed | timed_out
  -- Captured by wrap_up tool
  visitor_name TEXT,
  project_brief TEXT,
  fit_score TEXT,                          -- 'strong', 'partial', 'weak'
  action_items JSONB,
  expires_at TIMESTAMPTZ NOT NULL          -- now() + 24h, for cleanup
);
CREATE INDEX idx_calls_client_ip_started ON calls(client_ip, started_at);
CREATE INDEX idx_calls_expires ON calls(expires_at) WHERE status != 'deleted';

-- Optional: full transcript (mostly for debugging)
CREATE TABLE call_messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  role TEXT NOT NULL,                      -- 'agent' | 'visitor'
  content TEXT NOT NULL,
  ts_offset_ms INT NOT NULL,               -- ms from call start
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The two downloadable artifacts per call
CREATE TABLE call_artifacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                      -- 'summary_pdf' | 'recording_mp3'
  file_path TEXT NOT NULL,                 -- /data/calls/<id>/summary.pdf
  size_bytes INT,
  download_token TEXT UNIQUE NOT NULL,     -- random URL-safe token
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Knowledge base documents (mirrors DocuAI's schema)
CREATE TABLE knowledge_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  filename TEXT NOT NULL,
  status TEXT NOT NULL,                    -- pending | processing | indexed | failed
  page_count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  -- no expires_at: knowledge docs are permanent, only call artifacts cleanup
);
```

Note: the knowledge documents do NOT auto-delete. The 24h cleanup applies only to `calls` and `call_artifacts`.

### OpenSearch index

Single index `voicegen_knowledge`. Same schema as DocuAI's `documents` index — one document per page, with fields:
- `document_id`, `filename`, `page_number`
- `summary` (short, for retrieval ranking)
- `full_content` (verbatim VLM extraction, for the agent to quote)
- `embedding` (kNN vector, 384-dim from MiniLM)

---

## Deepgram Voice Agent configuration

The agent is defined **entirely in our code**. The browser opens a WebSocket to `wss://agent.deepgram.com/`, authenticates with a short-lived JWT minted by our `/call/start` endpoint, and sends a `Settings` message that configures everything. No Deepgram dashboard step.

### Settings message (sent on WebSocket open)

The complete Settings JSON our browser client sends after the `Welcome` message:

```json
{
  "type": "Settings",
  "audio": {
    "input":  { "encoding": "linear16", "sample_rate": 16000 },
    "output": { "encoding": "mp3", "sample_rate": 24000, "bitrate": 48000, "container": "none" }
  },
  "agent": {
    "language": "en",
    "listen": {
      "provider": {
        "type": "deepgram",
        "model": "flux-general-en",
        "version": "v2",
        "eot_threshold": 0.8
      }
    },
    "think": {
      "provider": {
        "type": "open_ai",
        "model": "gpt-4o-mini",
        "temperature": 0.6
      },
      "prompt": "<system prompt — see below>",
      "context_length": 8000,
      "functions": [
        {
          "name": "search_background",
          "description": "Search Moazzam's resume and project documentation for information relevant to the visitor's project. Use this any time you need to describe Moazzam's experience, past projects, or capabilities. Always call this before making specific claims.",
          "parameters": {
            "type": "object",
            "properties": {
              "query": {
                "type": "string",
                "description": "Natural language query describing what to look up. Examples: 'multi-agent research systems', 'experience with LangGraph', 'past RAG projects in healthcare'."
              },
              "top_k": {
                "type": "integer",
                "description": "Max number of passages to return. Default 3.",
                "default": 3
              }
            },
            "required": ["query"]
          }
        },
        {
          "name": "wrap_up",
          "description": "Call this when you have gathered enough information to end the call: visitor name, clear project description, rough timeline, and a fit assessment. After this, the call ends gracefully.",
          "parameters": {
            "type": "object",
            "properties": {
              "visitor_name":  { "type": "string" },
              "project_brief": { "type": "string", "description": "2-4 sentences in the visitor's own words." },
              "fit_score":     { "type": "string", "enum": ["strong", "partial", "weak"] },
              "fit_reasoning": { "type": "string", "description": "1-2 sentences explaining the fit score." },
              "action_items":  { "type": "array", "items": { "type": "string" }, "description": "2-3 concrete next steps." }
            },
            "required": ["visitor_name", "project_brief", "fit_score", "action_items"]
          }
        }
      ]
    },
    "speak": {
      "provider": {
        "type": "deepgram",
        "model": "aura-2-thalia-en",
        "speed": 1.0
      }
    },
    "greeting": "Hey there! I'm Moazzam's AI assistant. What kind of project are you thinking about working with him on?"
  }
}
```

### Agent system prompt

The `agent.think.prompt` field above. The actual string lives in [services/api/src/api/agent/prompts.py](../services/api/src/api/agent/prompts.py) so it can be edited and pushed without dashboard hops:

```
You are an AI customer-support assistant for Moazzam Qureshi, a senior AI
engineer who builds production-grade AI agents and RAG systems for clients
on Upwork and direct engagements.

Your job on this call:
1. Listen to the visitor's project description. Ask one or two short clarifying
   questions if needed (budget range, timeline, must-have features).
2. Use the `search_background` function to find Moazzam's past projects most
   relevant to what the visitor described. Quote concrete details from
   what you find — what Moazzam built, what problems it solved, what
   tech stack was used.
3. Give an honest fit assessment: strong, partial, or weak. Don't oversell.
   A weak fit honestly stated is more valuable than a strong fit overstated.
4. Propose concrete next steps (typically: a written project brief,
   intro call with Moazzam, or a referral if it's not a fit).
5. When you have the visitor's name, a clear project description, a rough
   timeline, and a fit assessment, call `wrap_up` to end gracefully.

Style:
- Conversational, not robotic. Pause naturally. Don't list bullets out loud.
- Specific over general. "Moazzam built a hybrid BM25 + kNN search system
  that cut retrieval latency by 60%" beats "Moazzam has experience with search."
- Honest. If you don't find anything relevant in the knowledge base, say so plainly.

Hard rules:
- Never promise that Moazzam personally will do something on a specific date.
  Always say "Moazzam will receive your project summary and respond within
  his usual response window."
- Never invent project details. Only describe things that came back from
  `search_background`.
- Don't quote rates. If asked, say rates depend on scope and Moazzam will
  share them after reviewing the project brief.
- Keep the call under 90 seconds total. If you sense the conversation is
  drifting, gently guide back to the project.
```

### How function calling actually flows

Both functions are configured with **`client_side: true` (implicit, since we don't supply an endpoint object)**. The browser is the orchestrator:

1. The agent decides to call `search_background`. Deepgram sends:
   ```json
   {"type": "FunctionCallRequest",
    "functions": [{"id": "fc_abc...", "name": "search_background",
                   "arguments": "{\"query\": \"voice agents\"}",
                   "client_side": true}]}
   ```
2. The browser parses the args, makes `POST /agent/search` to our FastAPI with bearer auth.
3. Our endpoint runs the OpenSearch hybrid query, returns top-k passages.
4. The browser wraps the result and sends back over the WebSocket:
   ```json
   {"type": "FunctionCallResponse",
    "id": "fc_abc...",
    "content": "<JSON-stringified passages>"}
   ```
5. The agent integrates the result into its next spoken turn.

Same flow for `wrap_up`. After `wrap_up`'s response, the browser knows the call is ending — it stops `MediaRecorder`, uploads the recording, navigates to the downloads screen.

Response shape from `/agent/search`:

```json
{
  "passages": [
    {
      "source": "project-docuai.pdf",
      "page": 3,
      "summary": "Moazzam built DocuAI...",
      "content": "DocuAI is a production agentic RAG system using OpenSearch hybrid search (BM25 + kNN), vision-LLM page extraction with Qwen 2.5 VL, and a LangGraph agent with explicit tools. Deployed on Coolify with Cloudflare Turnstile and per-IP cost ceilings."
    }
  ]
}
```

---

## API endpoints (our FastAPI service)

### `POST /call/start`

Called by the browser right before opening the Deepgram WebSocket.

**Request:**
```json
{
  "turnstile_token": "<token from Turnstile widget>"
}
```

**Server actions:**
1. Verify Turnstile token (Cloudflare siteverify)
2. Check IP rate limit (max 2 calls/IP/day from Redis)
3. Check global cost ceiling (Redis Lua counter — counts cents spent today)
4. Create a `calls` row, mark `status='in_progress'`, set `expires_at = now() + 24h`
5. Mint a short-lived Deepgram JWT via `POST https://api.deepgram.com/v1/auth/grant` (TTL 300s — long enough to connect, short enough to be safe)
6. Mint a `call_session_token` (random URL-safe, stored in Redis with the `call_id`, 90s TTL) — the browser sends this back on `/agent/search`, `/agent/wrap-up`, `/calls/{id}/recording` so we can authenticate WITHOUT exposing `AGENT_TOOL_SECRET` to the client
7. Return `{ call_id, deepgram_token, call_session_token, settings_json }`

`settings_json` is the full Deepgram Settings message above with the system prompt and function definitions filled in. Building it server-side keeps the prompt + function schema out of the JS bundle (so visitors can't read it via devtools).

**Failure modes:**
- 403 if Turnstile fails or per-IP rate limit hit
- 503 if global cost ceiling hit

### `POST /agent/search`

Called by the **browser** (not Deepgram) when the agent emits a `FunctionCallRequest` for `search_background`. Authenticated with `X-Call-Session-Token: <token>` header.

**Server actions:**
1. Verify `call_session_token` against Redis, look up `call_id`
2. Run hybrid search: embed query (MiniLM, CPU) → OpenSearch BM25+kNN → return top-k passages
3. Append a `call_messages` row (`role='tool'`, content describes the tool call) for the transcript record

### `POST /agent/wrap-up`

Called by the browser when the agent emits the `wrap_up` function call. Same session-token auth as `/agent/search`.

**Server actions:**
1. Verify `call_session_token`
2. Update the `calls` row with `visitor_name`, `project_brief`, `fit_score`, `fit_reasoning`, `action_items`
3. Enqueue Dramatiq actor `generate_summary_pdf(call_id)`
4. Return `{"acknowledged": true}` — the browser feeds this back to Deepgram as the function response so the agent can speak its closing line

### `POST /calls/{call_id}/recording`

Called by the browser at end-of-call with the MediaRecorder blob. Multipart upload. Session-token auth.

**Server actions:**
1. Verify `call_session_token` matches `call_id`
2. Cap upload size (default 8MB — 90s @ 64kbps mp3 is ~720KB; 8MB gives plenty of margin)
3. Stream the blob to `/data/calls/<call_id>/recording.mp3`
4. Insert `call_artifacts` row with kind=`recording_mp3` and a fresh `download_token`
5. Update `calls.ended_at`, `duration_seconds`, `status='completed'`
6. Enqueue `notify_discord(call_id)` Dramatiq actor

### `GET /artifacts/{download_token}`

Serves a single artifact (PDF or MP3). 24h TTL via the parent call's `expires_at`.

**Server actions:**
1. Look up the `download_token`
2. Verify the parent call hasn't expired
3. Stream the file with `Content-Disposition: attachment`

### `GET /calls/{call_id}` (client polls during/after call)

Returns the call's current status and any ready artifacts. Used by the wrap-up screen to know when the PDF is ready.

```json
{
  "call_id": "uuid",
  "status": "completed",
  "visitor_name": "...",
  "project_brief": "...",
  "fit_score": "strong",
  "action_items": [...],
  "artifacts": {
    "summary_pdf":   "/artifacts/<token>",
    "recording_mp3": "/artifacts/<token>"
  }
}
```

---

## Worker actors

### `generate_summary_pdf(call_id)`

1. Load call data + the conversation transcript from our `call_messages` table (the canonical record since Deepgram's WebSocket streams `ConversationText` messages we persist live)
2. Render WeasyPrint HTML template `templates/summary.html` with:
   - **Section 1** — The project (visitor's own words from `project_brief`)
   - **Section 2** — Why Moazzam is a fit (cites passages the agent surfaced)
   - **Section 3** — Suggested next steps (from `action_items`)
   - **Section 4** — Moazzam's contact details (email, Upwork profile URL, GitHub)
   - Footer: "Generated by VoiceGen AI · github.com/moazzam-qureshi"
3. Write PDF to `/data/calls/<call_id>/summary.pdf`
4. Create `call_artifacts` row with fresh `download_token`
5. Enqueue `notify_discord(call_id)` if not yet sent

### `notify_discord(call_id)`

POST to the Discord webhook URL with an embed:

```json
{
  "embeds": [{
    "title": "New VoiceGen lead: <visitor_name>",
    "color": 5814783,
    "fields": [
      { "name": "Fit", "value": "<fit_score>", "inline": true },
      { "name": "Duration", "value": "<duration_seconds>s", "inline": true },
      { "name": "Project brief", "value": "<project_brief truncated to 1024>" },
      { "name": "Action items", "value": "• <item1>\n• <item2>" }
    ]
  }],
  "components": [
    {
      "type": 1,
      "components": [
        { "type": 2, "label": "Summary PDF", "style": 5, "url": "<artifact url>" },
        { "type": 2, "label": "Recording", "style": 5, "url": "<artifact url>" }
      ]
    }
  ]
}
```

### `cleanup_expired_calls()` (APScheduler, hourly)

Same pattern as DocuAI:
1. Find all `calls` where `expires_at < now()` and `status != 'deleted'`
2. Delete files under `/data/calls/<call_id>/`
3. Mark `status='deleted'`

### `ingest_knowledge_doc(document_id)` (manual upload only)

Identical to DocuAI's ingest actor. Renders each page as image → Qwen 2.5 VL extracts `summary` + `full_content` → indexed in OpenSearch + embedding. Knowledge documents are uploaded once via an admin endpoint (or directly to OpenSearch from a CLI script), not by visitors.

---

## Environment variables

```bash
# === Standard from DocuAI ===
DATABASE_URL=postgresql+asyncpg://...
REDIS_URL=redis://redis:6379/0
OPENSEARCH_HOST=opensearch
OPENSEARCH_PORT=9200
OPENSEARCH_INDEX=voicegen_knowledge
OPENROUTER_API_KEY=sk-or-...             # used by VLM ingest of knowledge PDFs
OPENROUTER_VLM_MODEL=qwen/qwen2.5-vl-72b-instruct
TRUSTED_PROXIES=127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
LOG_LEVEL=INFO

# === Turnstile (standard, see CLAUDE.md Turnstile section) ===
TURNSTILE_SECRET=...
TURNSTILE_SITEKEY=...
NEXT_PUBLIC_TURNSTILE_SITEKEY=...

# === VoiceGen-specific ===
DEEPGRAM_API_KEY=...                     # used server-side to mint grant tokens
DEEPGRAM_LLM_PROVIDER=open_ai            # think.provider.type — also: anthropic, google, etc.
DEEPGRAM_LLM_MODEL=gpt-4o-mini           # think.provider.model
DEEPGRAM_TTS_MODEL=aura-2-thalia-en      # speak.provider.model
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
PUBLIC_BASE_URL=https://voicegen-ai.<domain>

# === Demo limits ===
CALL_MAX_PER_IP_PER_DAY=2
CALL_MAX_SECONDS=90
CALL_TTL_HOURS=24
GLOBAL_DAILY_COST_USD_LIMIT=10           # hard ceiling for Deepgram spend per UTC day

# === Frontend (Next.js build-time) ===
NEXT_PUBLIC_API_BASE_URL=
NEXT_PUBLIC_TURNSTILE_SITEKEY=...
# Note: no NEXT_PUBLIC_DEEPGRAM_* — the API key never reaches the browser.
# The browser gets its short-lived JWT from /call/start.
```

---

## Cost model

Deepgram Voice Agent pricing (verify before launch):
- Roughly $0.20/minute combined (Flux STT + Aura-2 TTS + LLM, with OpenAI gpt-4o-mini as the think provider). Configurable down via think-provider swaps.
- A full 90-second call ≈ $0.30
- **Moazzam has $1,200 of Deepgram credit, so this isn't real cash spend until the credit runs out (~6,000 minutes of conversation, ~4,000 demo calls).**

OpenSearch hybrid search per call:
- Typically 2-4 `search_background` invocations per call
- Each query: 1 embedding + 1 hybrid search ≈ <$0.001
- Negligible

VLM page extraction (one-time, during knowledge base setup):
- Resume + ~9 project PDFs × ~5 pages each ≈ 50 pages × $0.005 = ~$0.25 total, one-time

**With the credit, we can comfortably raise `GLOBAL_DAILY_COST_USD_LIMIT` to ~$30/day (~100 calls/day worst case) without burning real money.** The ceiling still exists to protect against abuse-driven runaway spend after the credit is gone.

---

## Knowledge base content plan

You'll author markdown for each, convert to PDF (any tool — pandoc, VS Code Markdown PDF, browser print), then upload via the admin endpoint:

1. `resume.pdf` — your standard CV
2. `project-docuai.pdf` — DocuAI deep dive (what, how, why-it-was-hard, outcome)
3. `project-voicegen.pdf` — VoiceGen AI itself (yes, this project should be in its own KB so the agent can talk about it)
4. Plus one PDF per significant prior project / consulting engagement worth surfacing

Aim for **1-2 paragraphs per project covering**:
- What it does (the problem in client-facing language)
- Tech choices and the reasoning
- A specific hard thing you solved
- The outcome / metric / customer value

That's what the agent needs to quote when it makes a fit assessment.

---

## Risks and open questions

1. **Deepgram region latency.** Their agent endpoint is at `wss://agent.deepgram.com/` (US) and `wss://api.eu.deepgram.com/v1/agent/converse` (EU). The browser-side SDK should pick the closer endpoint automatically, but we should verify EU/Asia visitor latency is acceptable (<300ms RTT to first audio byte). Test post-deploy from a couple of geographies.

2. **Function-call latency budget.** The agent will pause speech while `search_background` runs. Need to keep p95 latency on `/agent/search` under 400ms to avoid awkward gaps. OpenSearch hybrid is fast (~50ms); the cost is embedding the query (~50-100ms with MiniLM on CPU). The browser-as-orchestrator round-trip adds one extra hop vs. server-to-server tool calls, but on a colocated browser+API the added RTT is <50ms.

3. **MediaRecorder format support.** Browser MediaRecorder records to whatever the browser supports — Chrome/Edge prefer `audio/webm;codecs=opus`, Safari prefers `audio/mp4`. We capture in whichever format the browser offers, store as-is, and serve with the correct Content-Type on the download endpoint. The user gets a playable file regardless. Conversion to a uniform mp3 (e.g. via `ffmpeg` worker pass) is a possible Phase 2 polish but not needed for portfolio screenshots.

4. **Mixing visitor mic + agent TTS in MediaRecorder.** Both audio streams need to be routed through a single `MediaStreamDestination` of an `AudioContext` so MediaRecorder captures the mixed conversation, not just one side. This is a known Web Audio pattern but worth verifying on Safari, which has historical quirks with cross-origin TTS streams.

5. **Discord webhook rate limits.** Discord limits incoming webhook calls to ~30/min. At our scale this is irrelevant, but flagged for future-proofing.

6. **Spam protection beyond Turnstile.** A determined attacker with rotating IPs and solved Turnstile challenges can still burn cost. The `GLOBAL_DAILY_COST_USD_LIMIT` is the last line of defense. With the $1,200 credit, the credit itself becomes a soft ceiling — once exhausted, Deepgram bills cash, and the daily limit kicks in to cap that.
