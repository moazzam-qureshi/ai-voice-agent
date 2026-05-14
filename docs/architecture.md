# VoiceGen AI — Architecture

**Product:** VoiceGen AI
**Repo:** `ai-voice-agent/`
**Tagline:** An AI customer support agent for Moazzam's freelancing business
**Status:** Pre-build, architecture locked, scaffolding pending

---

## What it is

A visitor lands on the page, clicks **"Talk to my AI assistant"**, and a voice call starts in the browser. The agent asks what the visitor's project is, then uses RAG over Moazzam's resume and per-project documentation to surface relevant past work, qualify fit, and propose next steps. At call end, the visitor downloads a branded PDF summary plus the audio recording. A Discord webhook fires to Moazzam with the same payload.

The portfolio differentiator vs. a generic ElevenLabs demo: the agent doesn't use ElevenLabs' built-in knowledge base. It calls a custom RAG endpoint (OpenSearch + LangGraph, reusing DocuAI's stack) for every lookup. That's the AI-engineering story.

---

## System diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                       Browser (Next.js)                              │
│                                                                      │
│  Landing → [Talk to AI] → Turnstile → ElevenLabs WebRTC client      │
│                                       │                              │
│                                       │ audio in/out                 │
│                                       ▼                              │
│                              ┌─────────────────┐                     │
│                              │ Call UI         │                     │
│                              │  - waveform     │                     │
│                              │  - transcript   │                     │
│                              │  - wrap-up CTA  │                     │
│                              └────────┬────────┘                     │
└───────────────────────────────────────┼──────────────────────────────┘
                                        │
                  ┌─────────────────────┴─────────────────────┐
                  │                                            │
                  ▼                                            ▼
┌──────────────────────────────────┐         ┌────────────────────────────┐
│   ElevenLabs Conversational AI    │         │   VoiceGen API (FastAPI)   │
│                                   │         │                            │
│  - STT (Cloudflare Whisper)       │         │  POST /call/start          │
│  - LLM (OpenAI gpt-4o-mini)       │         │     → Turnstile verify     │
│  - TTS (ElevenLabs voice)         │         │     → rate/cost check      │
│  - Agent prompt + tools           │         │     → returns signed       │
│  - Server-side conversation       │         │       ElevenLabs token     │
│    transcript                     │         │                            │
│                                   │         │  POST /agent/search        │
│  Tools the agent can invoke:      │  ────►  │     (called by ElevenLabs  │
│   • search_background(query)      │         │      webhook tool)         │
│   • wrap_up(project_summary,      │         │     → LangGraph agent      │
│     fit_score, action_items)      │         │     → OpenSearch hybrid    │
│                                   │         │       search across docs   │
│                                   │  ────►  │     → returns top-k        │
│                                   │         │       passages + citations │
│                                   │         │                            │
│                                   │  ────►  │  POST /agent/wrap-up       │
│                                   │         │     (called when agent     │
│                                   │         │      decides to end)       │
│                                   │         │     → generates summary    │
│                                   │         │       PDF                  │
│                                   │         │     → finalizes recording  │
│                                   │         │     → fires Discord        │
│                                   │         │       webhook              │
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

- **Voice loop:** [ElevenLabs Conversational AI](https://elevenlabs.io/docs/conversational-ai/overview) (handles STT + LLM + TTS + WebRTC)
- **PDF generation:** WeasyPrint (HTML+CSS → PDF, brand-controllable, works headless in Docker)
- **Recording storage:** local filesystem under `/data/recordings/<call_id>.mp3`, served by FastAPI behind a short-lived signed URL
- **Discord:** plain HTTPS webhook, no SDK

Explicitly not used: ElevenLabs' built-in knowledge base feature. The agent's knowledge lookups go to our `/agent/search` endpoint via ElevenLabs' webhook tools.

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

## ElevenLabs Conversational AI configuration

The agent is configured **in ElevenLabs' dashboard**, not in our code, with these settings:

### Agent prompt (system message)

```
You are an AI customer-support assistant for Moazzam Qureshi, a senior AI
engineer who builds production-grade AI agents and RAG systems for clients
on Upwork and direct engagements.

Your job on this call:
1. Greet the visitor warmly and ask how Moazzam can help them.
2. Listen to their project description. Ask one or two short clarifying
   questions if needed (budget range, timeline, must-have features).
3. Use the `search_background` tool to find Moazzam's past projects most
   relevant to what the visitor described. Quote concrete details from
   what you find — what Moazzam built, what problems it solved, what
   tech stack was used.
4. Give an honest fit assessment: strong fit, partial fit, or weak fit.
   Don't oversell. A weak fit honestly stated is more valuable than a
   strong fit overstated.
5. Propose concrete next steps (typically: a written project brief,
   intro call with Moazzam, or a referral if it's not a fit).
6. When you have the visitor's name, a clear project description, a
   rough timeline, and a fit assessment, call `wrap_up` to end the
   call gracefully.

Style:
- Conversational, not robotic. Pause naturally. Don't list bullets out loud.
- Specific over general. "Moazzam built a hybrid BM25 + kNN search system
  that cut retrieval latency by 60%" beats "Moazzam has experience with
  search."
- Honest. If you don't find anything relevant in the knowledge base, say
  so plainly.

Hard rules:
- Never promise that Moazzam personally will do something on a specific
  date. Always say "Moazzam will receive your project summary and respond
  within his usual response window."
- Never invent project details. Only describe things that came back from
  `search_background`.
- Don't quote rates. If asked, say rates depend on scope and Moazzam will
  share them after reviewing the project brief.
- Keep the call under 90 seconds total. If you sense the conversation is
  drifting, gently guide back to the project.
```

### Tools registered on the agent

#### Tool 1: `search_background`

Called by the agent whenever it needs information about Moazzam's experience.

```json
{
  "name": "search_background",
  "description": "Search Moazzam's resume and project documentation for information relevant to the visitor's project. Use this any time you need to describe Moazzam's experience, past projects, or capabilities. Always call this before making specific claims.",
  "url": "https://api.voicegen-ai.<domain>/agent/search",
  "method": "POST",
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
  },
  "auth": {
    "type": "bearer",
    "token": "<server-side secret>"
  }
}
```

Response shape from our endpoint:

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

#### Tool 2: `wrap_up`

Called by the agent when it has enough information to end the call.

```json
{
  "name": "wrap_up",
  "description": "Call this when you have gathered enough information to end the call: the visitor's name, a clear project description, a rough timeline, and a fit assessment. This will generate the summary PDF and end the call.",
  "url": "https://api.voicegen-ai.<domain>/agent/wrap-up",
  "method": "POST",
  "parameters": {
    "type": "object",
    "properties": {
      "visitor_name": { "type": "string" },
      "project_brief": {
        "type": "string",
        "description": "The visitor's project in their own words, 2-4 sentences."
      },
      "fit_score": {
        "type": "string",
        "enum": ["strong", "partial", "weak"],
        "description": "Honest assessment of how well Moazzam's experience matches what the visitor needs."
      },
      "fit_reasoning": {
        "type": "string",
        "description": "1-2 sentences on why this fit score, with reference to relevant past projects."
      },
      "action_items": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Concrete next steps, typically 2-3 items."
      }
    },
    "required": ["visitor_name", "project_brief", "fit_score", "action_items"]
  },
  "auth": {
    "type": "bearer",
    "token": "<server-side secret>"
  }
}
```

---

## API endpoints (our FastAPI service)

### `POST /call/start`

Called by the browser before initiating the ElevenLabs WebRTC session.

**Request:**
```json
{
  "turnstile_token": "<token from Turnstile widget>"
}
```

**Server actions:**
1. Verify Turnstile token (Cloudflare siteverify)
2. Check IP rate limit (max 2 calls/IP/day from Redis)
3. Check global cost ceiling (Redis Lua counter)
4. Create a `calls` row, mark `status='in_progress'`
5. Mint a signed ElevenLabs WebRTC session token via their API
6. Return `{ call_id, elevenlabs_session_token, elevenlabs_agent_id }`

**Failure modes:**
- 403 if Turnstile fails or rate limit hit
- 503 if global cost ceiling hit

### `POST /agent/search`

Called by ElevenLabs as a tool. Authenticated with bearer token.

**Request:** as above.

**Server actions:**
1. Verify bearer token matches `AGENT_TOOL_SECRET`
2. Run LangGraph agent's `search` step: embed query → OpenSearch hybrid → return top-k passages
3. Log the call's tool use to `call_messages` (role='agent', content=`tool_call:search_background(...)`) for the transcript record

### `POST /agent/wrap-up`

Called by ElevenLabs as a tool when the agent decides to end.

**Server actions:**
1. Verify bearer token
2. Look up the `call_id` via the ElevenLabs `conversation_id` (passed in headers)
3. Update `calls` row with `visitor_name`, `project_brief`, `fit_score`, `action_items`, `status='completed'`
4. Enqueue Dramatiq actor `generate_summary_pdf(call_id)`
5. Return `{"acknowledged": true}` so the agent knows it can speak the closing line

### `POST /webhooks/elevenlabs/call-ended`

Called by ElevenLabs when the WebRTC session terminates. (Configured in the ElevenLabs dashboard.)

**Server actions:**
1. Verify the ElevenLabs webhook HMAC signature
2. Update `calls.ended_at`, `duration_seconds`, mark `status='completed'` if not already
3. Fetch the audio recording from ElevenLabs (their conversation audio download endpoint)
4. Store recording at `/data/calls/<call_id>/recording.mp3`
5. Create `call_artifacts` row for the recording with a fresh `download_token`
6. Enqueue `notify_discord(call_id)` Dramatiq actor

### `GET /artifacts/{download_token}`

Serves a single artifact (PDF or MP3). Token is single-pointer (24h TTL via `call_artifacts` row's `expires_at`).

**Server actions:**
1. Look up the `download_token`
2. Verify the parent call hasn't expired (cleanup deletes both row and file)
3. Stream the file with `Content-Disposition: attachment`

### `GET /calls/{call_id}` (client polls during/after call)

Returns the call's current status and any ready artifacts.

```json
{
  "call_id": "uuid",
  "status": "completed",
  "visitor_name": "...",
  "project_brief": "...",
  "fit_score": "strong",
  "action_items": [...],
  "artifacts": {
    "summary_pdf": "/artifacts/<token>",
    "recording_mp3": "/artifacts/<token>"
  }
}
```

---

## Worker actors

### `generate_summary_pdf(call_id)`

1. Load call data + the conversation transcript (from ElevenLabs API for the high-fidelity version, or our `call_messages` table as fallback)
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
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_VLM_MODEL=qwen/qwen2.5-vl-72b-instruct
TRUSTED_PROXIES=127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
LOG_LEVEL=INFO

# === Turnstile (standard, see CLAUDE.md Turnstile section) ===
TURNSTILE_SECRET=...
TURNSTILE_SITEKEY=...
NEXT_PUBLIC_TURNSTILE_SITEKEY=...

# === VoiceGen-specific ===
ELEVENLABS_API_KEY=...
ELEVENLABS_AGENT_ID=...                  # ID of the configured agent
ELEVENLABS_WEBHOOK_SECRET=...            # HMAC for verifying call-ended webhooks
AGENT_TOOL_SECRET=...                    # bearer token for tool calls
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
PUBLIC_BASE_URL=https://voicegen-ai.<domain>

# === Demo limits ===
CALL_MAX_PER_IP_PER_DAY=2
CALL_MAX_SECONDS=90
CALL_TTL_HOURS=24
GLOBAL_DAILY_COST_USD_LIMIT=10           # hard ceiling for ElevenLabs spend per UTC day

# === Frontend (Next.js build-time) ===
NEXT_PUBLIC_API_BASE_URL=
NEXT_PUBLIC_ELEVENLABS_AGENT_ID=...      # exposed so the WebRTC client can connect
```

---

## Cost model

ElevenLabs Conversational AI pricing (as of early 2026, verify before launch):
- ~$0.30 per minute of conversation (bundles STT + LLM + TTS)
- A full 90-second call ≈ $0.45

OpenSearch hybrid search per call:
- Typically 2-4 `search_background` invocations per call
- Each query: 1 embedding + 1 hybrid search ≈ <$0.001
- Negligible

VLM page extraction (one-time, during knowledge base setup):
- Resume + ~9 project PDFs × ~5 pages each ≈ 50 pages × $0.005 = ~$0.25 total, one-time

**Daily worst case at 2 calls/IP × 100 unique IPs = 200 calls × $0.45 = $90/day.** With `GLOBAL_DAILY_COST_USD_LIMIT=10` the system stops accepting new calls after ~22 calls/day. That's the cost ceiling.

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

1. **ElevenLabs Conversational AI region.** Their WebRTC infrastructure has lower latency in US-East than EU. Need to confirm acceptable latency for visitors in EU/Asia before launch. If unacceptable, fallback is to roll our own with Deepgram + OpenRouter, which we can do but ships ~3x slower.

2. **Webhook tool latency budget.** The agent will pause speech while `search_background` runs. Need to keep p95 latency on `/agent/search` under 400ms to avoid awkward gaps. OpenSearch hybrid is fast (~50ms); the cost is embedding the query (~50-100ms with MiniLM on CPU). Should be fine but worth measuring.

3. **Audio recording from ElevenLabs.** Confirm whether their API lets us download conversation audio post-call (most platforms do, but worth verifying upfront — if not, we need to record client-side via MediaRecorder API, which is more complex).

4. **Discord webhook rate limits.** Discord limits incoming webhook calls to ~30/min. At our scale (max 22 calls/day) this is irrelevant, but flagged for future-proofing.

5. **Spam protection beyond Turnstile.** A determined attacker with rotating IPs and solved Turnstile challenges can still burn cost. The `GLOBAL_DAILY_COST_USD_LIMIT` is the last line of defense. Should be sufficient for a portfolio demo; not sufficient for a real product.
