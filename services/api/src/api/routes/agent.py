"""Agent tool endpoints — called by the browser when Deepgram emits a
FunctionCallRequest.

Both routes authenticate via the X-Call-Session-Token header, which the
browser holds from /call/start. The token is Redis-backed and expires
with the call, so a leaked token has a ~3-minute exploitation window.

Endpoints:
- POST /agent/search   — search_background function
- POST /agent/wrap-up  — wrap_up function (terminal, revokes the token)
"""

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.call_session import verify_call_session
from api.db.opensearch_store import get_page_store
from api.db.session import get_db
from shared.db_models import Call, CallMessage, CallStatus

logger = structlog.get_logger(__name__)

router = APIRouter()


# === /agent/search ===========================================================


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(default=3, ge=1, le=10)


class SearchPassage(BaseModel):
    source: str
    page: int
    summary: str
    content: str


class SearchResponse(BaseModel):
    passages: list[SearchPassage]


@router.post("/agent/search", response_model=SearchResponse)
async def agent_search(
    body: SearchRequest,
    call_id: str = Depends(verify_call_session),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """Run hybrid search and log the tool-call into the transcript."""
    store = get_page_store()
    hits = store.hybrid_search(body.query, k=body.top_k)

    passages = [
        SearchPassage(
            source=hit["filename"],
            page=hit["page_number"],
            summary=hit["summary"],
            content=hit["full_content"],
        )
        for hit in hits
    ]

    # Persist the tool call as a transcript marker. ts_offset_ms is rough —
    # we don't know the call's exact start instant here, so use the elapsed
    # time since the Call row was created.
    msg = CallMessage(
        call_id=call_id,
        role="tool",
        content=f"search_background({body.query!r}) -> {len(passages)} passages",
        ts_offset_ms=0,
    )
    db.add(msg)

    logger.info(
        "agent_search_complete",
        call_id=call_id,
        query=body.query[:80],
        passage_count=len(passages),
    )

    return SearchResponse(passages=passages)


# === /agent/wrap-up ==========================================================


class WrapUpRequest(BaseModel):
    visitor_name: str = Field(..., min_length=1, max_length=255)
    project_brief: str = Field(..., min_length=1, max_length=2000)
    fit_score: str = Field(..., pattern="^(strong|partial|weak)$")
    fit_reasoning: str = Field(default="", max_length=2000)
    action_items: list[str] = Field(..., min_length=1, max_length=10)


class WrapUpResponse(BaseModel):
    acknowledged: bool


@router.post("/agent/wrap-up", response_model=WrapUpResponse)
async def agent_wrap_up(
    body: WrapUpRequest,
    call_id: str = Depends(verify_call_session),
    db: AsyncSession = Depends(get_db),
) -> WrapUpResponse:
    """Capture the agent's wrap-up summary and enqueue PDF generation.

    Note: the call session token is NOT revoked here. The browser still
    needs it moments later to POST the recording to /calls/{id}/recording.
    The token's natural Redis TTL handles cleanup, and the recording
    endpoint revokes it after a successful upload.
    """
    stmt = select(Call).where(Call.id == call_id)
    result = await db.execute(stmt)
    call = result.scalar_one_or_none()
    if call is None:
        raise HTTPException(status_code=404, detail="call_not_found")

    call.visitor_name = body.visitor_name
    call.project_brief = body.project_brief
    call.fit_score = body.fit_score
    call.fit_reasoning = body.fit_reasoning
    call.action_items = body.action_items
    call.status = CallStatus.COMPLETED.value
    call.ended_at = datetime.now(UTC)
    if call.started_at:
        delta = call.ended_at - call.started_at
        call.duration_seconds = int(delta.total_seconds())

    # Enqueue the summary-PDF actor. Late import so this module doesn't
    # fail to load if Phase 4 hasn't landed yet — the import is wrapped
    # so /agent/wrap-up still succeeds (the actor will be a no-op until
    # Phase 4 fills it in).
    try:
        from shared.tasks import generate_summary_pdf  # type: ignore[attr-defined]

        generate_summary_pdf.send(call_id=call_id)
        logger.info("summary_pdf_enqueued", call_id=call_id)
    except (ImportError, AttributeError):
        logger.warning(
            "summary_pdf_actor_not_yet_implemented",
            call_id=call_id,
            note="phase_4_pending",
        )

    logger.info(
        "agent_wrap_up_complete",
        call_id=call_id,
        visitor_name=body.visitor_name,
        fit_score=body.fit_score,
        action_items_count=len(body.action_items),
    )

    return WrapUpResponse(acknowledged=True)
