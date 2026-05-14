"""GET /artifacts/{download_token} — public, untyped, single-pointer.

Looks up the artifact by token, verifies the parent Call hasn't expired
(cleanup deletes the file when expires_at < now), streams the file.

Tokens are 32-byte URL-safe randoms with ~256 bits of entropy. They live
exactly as long as the parent Call's 24h TTL. Brute-force isn't a
plausible threat.
"""

from datetime import UTC, datetime
from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from api.db.session import get_db
from shared.db_models import Call, CallArtifact, CallStatus

logger = structlog.get_logger(__name__)

router = APIRouter()


_CONTENT_TYPE_FOR_EXT = {
    ".webm": "audio/webm",
    ".m4a":  "audio/mp4",
    ".mp3":  "audio/mpeg",
    ".ogg":  "audio/ogg",
    ".pdf":  "application/pdf",
}


_FILENAME_FOR_KIND = {
    "summary_pdf":   "voicegen-summary",
    "recording_mp3": "voicegen-recording",
}


@router.get("/artifacts/{download_token}")
async def download_artifact(
    download_token: str,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(CallArtifact, Call)
        .join(Call, Call.id == CallArtifact.call_id)
        .where(CallArtifact.download_token == download_token)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="artifact_not_found")

    artifact, call = row

    if call.status == CallStatus.DELETED.value or call.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=410, detail="artifact_expired")

    file_path = Path(artifact.file_path)
    if not file_path.exists():
        # File missing on disk but row still here — treat as expired.
        logger.warning(
            "artifact_file_missing",
            artifact_id=artifact.id,
            expected_path=str(file_path),
        )
        raise HTTPException(status_code=410, detail="artifact_expired")

    ext = file_path.suffix.lower()
    content_type = _CONTENT_TYPE_FOR_EXT.get(ext, "application/octet-stream")
    download_name_stem = _FILENAME_FOR_KIND.get(artifact.kind, "voicegen-artifact")
    download_name = f"{download_name_stem}-{artifact.call_id[:8]}{ext}"

    return FileResponse(
        path=file_path,
        media_type=content_type,
        filename=download_name,
    )
