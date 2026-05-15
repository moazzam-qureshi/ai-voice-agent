"""POST /agent/transcript — browser sends each ConversationText turn so it
lives in the canonical DB record alongside the tool-call markers.

The Deepgram WebSocket sends ConversationText messages with role +
content. We persist them here so the PDF generator (worker) and any
post-call analytics have the full conversation transcript, not just the
ephemeral React state in the browser.

Called by voice-agent.ts in a fire-and-forget pattern — non-blocking,
errors are logged but don't break the call.
"""

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.call_session import verify_call_session
from api.db.session import get_db
from shared.db_models import CallMessage

logger = structlog.get_logger(__name__)

router = APIRouter()


class TranscriptTurnIn(BaseModel):
    role: str = Field(..., pattern="^(agent|visitor|tool)$")
    content: str = Field(..., min_length=1, max_length=4000)
    ts_offset_ms: int = Field(default=0, ge=0)


class TranscriptResponse(BaseModel):
    persisted: bool


@router.post("/agent/transcript", response_model=TranscriptResponse)
async def append_transcript(
    body: TranscriptTurnIn,
    call_id: str = Depends(verify_call_session),
    db: AsyncSession = Depends(get_db),
) -> TranscriptResponse:
    msg = CallMessage(
        call_id=call_id,
        role=body.role,
        content=body.content,
        ts_offset_ms=body.ts_offset_ms,
    )
    db.add(msg)
    return TranscriptResponse(persisted=True)
