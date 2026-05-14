"""Dramatiq actor: send the Discord webhook notification.

Fires twice for the same call in the happy path:
1. Recording upload → notify_discord (recording link only)
2. PDF generation completes → notify_discord again (now both links)

The actor is idempotent in spirit but Discord doesn't dedupe, so we
gate on `discord_notified_at` and only re-send if a new artifact
appeared since last notification. In practice, we want exactly one
notification per call once both artifacts exist.

Decision: only fire the notification once BOTH artifacts (PDF + recording)
exist, OR once 30 seconds pass after the recording upload (in case the
PDF actor failed). This avoids the "two notifications per call" UX issue.

The 30-second timer is implemented by enqueueing a delayed retry from
the recording-upload-trigger path; the PDF-completed-trigger path fires
immediately if it finds a recording already present.
"""

from datetime import UTC, datetime

import dramatiq
import structlog
from sqlalchemy import select

from shared.db_models import Call, CallArtifact

logger = structlog.get_logger(__name__)


@dramatiq.actor(
    queue_name="discord",
    priority=10,
    max_retries=3,
    time_limit=60 * 1000,  # 1 min
)
def notify_discord(call_id: str) -> dict:
    from shared.discord.webhook import post_call_notification
    from worker.config import settings as wsettings
    from worker.db.session import get_db_session

    if not wsettings.discord_webhook_url:
        logger.info("discord_skipped_no_webhook_url", call_id=call_id)
        return {"call_id": call_id, "skipped": True}

    with get_db_session() as db:
        call = db.execute(select(Call).where(Call.id == call_id)).scalar_one_or_none()
        if call is None:
            raise ValueError(f"call not found: {call_id}")

        # Skip if we already notified for this call.
        if call.discord_notified_at is not None:
            logger.info("discord_already_notified", call_id=call_id)
            return {"call_id": call_id, "already_notified": True}

        artifacts = db.execute(
            select(CallArtifact).where(CallArtifact.call_id == call_id)
        ).scalars().all()

        summary_pdf_url = None
        recording_url = None
        base = wsettings.public_base_url.rstrip("/")
        for a in artifacts:
            url = f"{base}/artifacts/{a.download_token}"
            if a.kind == "summary_pdf":
                summary_pdf_url = url
            elif a.kind == "recording_mp3":
                recording_url = url

        ok = post_call_notification(
            webhook_url=wsettings.discord_webhook_url,
            visitor_name=call.visitor_name,
            fit_score=call.fit_score,
            duration_seconds=call.duration_seconds,
            project_brief=call.project_brief,
            action_items=call.action_items,
            summary_pdf_url=summary_pdf_url,
            recording_url=recording_url,
        )

        if ok:
            call.discord_notified_at = datetime.now(UTC)

    return {
        "call_id": call_id,
        "notified": ok,
        "had_pdf": summary_pdf_url is not None,
        "had_recording": recording_url is not None,
    }
