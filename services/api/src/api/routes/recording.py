"""POST /calls/{call_id}/recording — browser uploads the MediaRecorder blob.

The browser captures a mixed audio stream (visitor mic + Deepgram TTS)
via AudioContext + MediaRecorder during the call, then POSTs the blob
here once the call ends. We store it on disk, register a CallArtifact
row with a download token, and enqueue the Discord notification actor.

Content-Type from MediaRecorder varies by browser:
  - Chrome/Edge: audio/webm;codecs=opus
  - Safari:      audio/mp4
  - Firefox:     audio/ogg;codecs=opus

We store whatever the browser sends, with extension matching the type.
The download endpoint serves with the same Content-Type so the user
gets a playable file on their platform.
"""

import secrets
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Path as FPath, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.call_session import verify_call_session_for_call_id
from api.config import settings
from api.db.session import get_db
from shared.db_models import Call, CallArtifact, CallStatus

logger = structlog.get_logger(__name__)

router = APIRouter()


_EXTENSION_FOR_TYPE = {
    "audio/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
}


def _extension_from_content_type(content_type: str | None) -> str:
    """Strip any codec suffix and look up a known audio mime type."""
    if not content_type:
        return ".webm"
    base = content_type.split(";")[0].strip().lower()
    return _EXTENSION_FOR_TYPE.get(base, ".webm")


class RecordingUploadResponse(BaseModel):
    download_token: str
    size_bytes: int


@router.post("/calls/{call_id}/recording", response_model=RecordingUploadResponse)
async def upload_recording(
    call_id: str = FPath(...),
    file: UploadFile = File(...),
    _: str = Depends(verify_call_session_for_call_id),
    db: AsyncSession = Depends(get_db),
) -> RecordingUploadResponse:
    stmt = select(Call).where(Call.id == call_id)
    result = await db.execute(stmt)
    call = result.scalar_one_or_none()
    if call is None:
        raise HTTPException(status_code=404, detail="call_not_found")

    # Stream the body to disk, enforcing the size cap chunk-by-chunk so
    # we don't buffer multi-MB uploads in memory.
    call_dir = Path(settings.data_dir) / "calls" / call_id
    call_dir.mkdir(parents=True, exist_ok=True)
    extension = _extension_from_content_type(file.content_type)
    file_path = call_dir / f"recording{extension}"

    total_bytes = 0
    chunk_size = 256 * 1024
    try:
        with file_path.open("wb") as out:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > settings.recording_max_bytes:
                    out.close()
                    file_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Recording exceeds the {settings.recording_max_bytes // 1024 // 1024} MB cap."
                        ),
                    )
                out.write(chunk)
    finally:
        await file.close()

    if total_bytes == 0:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="empty_recording")

    download_token = secrets.token_urlsafe(32)
    artifact = CallArtifact(
        call_id=call_id,
        kind="recording_mp3",
        file_path=str(file_path),
        size_bytes=total_bytes,
        download_token=download_token,
    )
    db.add(artifact)

    # Make sure the Call is marked completed (the agent's wrap_up should
    # already have done this, but the recording can arrive in any order
    # relative to wrap-up).
    if call.status == CallStatus.IN_PROGRESS.value:
        call.status = CallStatus.COMPLETED.value

    await db.flush()

    # Fire Discord notification if Phase 4 has landed.
    try:
        from shared.tasks import notify_discord  # type: ignore[attr-defined]

        notify_discord.send(call_id=call_id)
        logger.info("discord_notify_enqueued", call_id=call_id)
    except (ImportError, AttributeError):
        logger.warning(
            "discord_notify_actor_not_yet_implemented",
            call_id=call_id,
            note="phase_4_pending",
        )

    logger.info(
        "recording_uploaded",
        call_id=call_id,
        size_bytes=total_bytes,
        extension=extension,
    )

    return RecordingUploadResponse(
        download_token=download_token,
        size_bytes=total_bytes,
    )
