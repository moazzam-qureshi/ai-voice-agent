"""GET /calls/{call_id} — polled by the wrap-up screen while waiting for
the PDF, also gives the downloads screen its two artifact URLs.

call_id is treated as a secret on the browser (browser holds it from the
/call/start response; nobody else should know it). 256-bit UUID entropy
makes guessing impractical.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.db.session import get_db
from shared.db_models import Call, CallArtifact, CallStatus

router = APIRouter()


class ArtifactUrls(BaseModel):
    summary_pdf: str | None = None
    recording_mp3: str | None = None


class CallStatusResponse(BaseModel):
    call_id: str
    status: str
    visitor_name: str | None
    project_brief: str | None
    fit_score: str | None
    fit_reasoning: str | None
    action_items: list[str] | None
    duration_seconds: int | None
    artifacts: ArtifactUrls


def _artifact_url(token: str) -> str:
    """Build a fully-qualified download URL from a token."""
    base = settings.public_base_url.rstrip("/")
    return f"{base}/artifacts/{token}"


@router.get("/calls/{call_id}", response_model=CallStatusResponse)
async def get_call_status(
    call_id: str,
    db: AsyncSession = Depends(get_db),
) -> CallStatusResponse:
    stmt = select(Call).where(Call.id == call_id)
    result = await db.execute(stmt)
    call = result.scalar_one_or_none()
    if call is None:
        raise HTTPException(status_code=404, detail="call_not_found")

    if call.status == CallStatus.DELETED.value or call.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=410, detail="call_expired")

    art_stmt = select(CallArtifact).where(CallArtifact.call_id == call_id)
    art_result = await db.execute(art_stmt)
    artifacts = art_result.scalars().all()

    summary_url: str | None = None
    recording_url: str | None = None
    for a in artifacts:
        url = _artifact_url(a.download_token)
        if a.kind == "summary_pdf":
            summary_url = url
        elif a.kind == "recording_mp3":
            recording_url = url

    return CallStatusResponse(
        call_id=call.id,
        status=call.status,
        visitor_name=call.visitor_name,
        project_brief=call.project_brief,
        fit_score=call.fit_score,
        fit_reasoning=call.fit_reasoning,
        action_items=call.action_items,
        duration_seconds=call.duration_seconds,
        artifacts=ArtifactUrls(
            summary_pdf=summary_url,
            recording_mp3=recording_url,
        ),
    )
