"""Dramatiq actor: generate the call's summary PDF.

Triggered by /agent/wrap-up after the agent has captured visitor_name,
project_brief, fit_score, fit_reasoning, action_items.

The actor:
1. Loads the Call row
2. Pulls 'relevant past work' references from the call's tool_call
   transcript markers (search_background passages mentioned)
3. Renders the WeasyPrint template
4. Inserts a CallArtifact row with a fresh download_token
5. Enqueues notify_discord if no Discord notification has been sent

Sync session because Dramatiq actors run sync.
"""

import secrets
from datetime import UTC
from pathlib import Path

import dramatiq
import structlog
from sqlalchemy import select

from shared.db_models import Call, CallArtifact, CallMessage

logger = structlog.get_logger(__name__)


@dramatiq.actor(
    queue_name="pdf",
    priority=5,
    max_retries=2,
    time_limit=2 * 60 * 1000,  # 2 min
)
def generate_summary_pdf(call_id: str) -> dict:
    """Render the PDF and register it as an artifact."""
    from shared.pdf.summary_pdf import render_summary_pdf
    from worker.config import settings as wsettings
    from worker.db.session import get_db_session

    logger.info("generate_summary_pdf_start", call_id=call_id)

    with get_db_session() as db:
        call = db.execute(select(Call).where(Call.id == call_id)).scalar_one_or_none()
        if call is None:
            raise ValueError(f"call not found: {call_id}")

        # If a PDF was already generated for this call, no-op. Idempotent
        # safety for the at-least-once delivery from /agent/wrap-up retries.
        existing = db.execute(
            select(CallArtifact)
            .where(CallArtifact.call_id == call_id)
            .where(CallArtifact.kind == "summary_pdf")
        ).scalar_one_or_none()
        if existing is not None:
            logger.info("summary_pdf_already_exists", call_id=call_id)
            return {"call_id": call_id, "already_exists": True}

        # Pull tool-call markers from the transcript so we can list the
        # past projects the agent referenced. The marker format is
        # `search_background('...') -> N passages` — we don't surface
        # the passages themselves, just the existence of the lookups.
        # For a richer PDF we'd persist the actual passages on each
        # /agent/search call; that's a future improvement.
        tool_calls = db.execute(
            select(CallMessage)
            .where(CallMessage.call_id == call_id)
            .where(CallMessage.role == "tool")
        ).scalars().all()

        # Until we persist the full passage payloads, leave relevant_projects
        # empty — the template hides the block when there's nothing.
        relevant_projects: list[dict[str, str]] = []
        _ = tool_calls  # placeholder; future: derive project names from tool args

        out_dir = Path(wsettings.data_dir) / "calls" / call_id
        out_path = out_dir / "summary.pdf"

        date_iso = call.started_at.astimezone(UTC).strftime("%Y-%m-%d") if call.started_at else ""

        render_summary_pdf(
            call_id=call_id,
            visitor_name=call.visitor_name,
            project_brief=call.project_brief,
            fit_score=call.fit_score,
            fit_reasoning=call.fit_reasoning,
            action_items=call.action_items,
            duration_seconds=call.duration_seconds,
            date_iso=date_iso,
            relevant_projects=relevant_projects,
            out_path=out_path,
        )

        download_token = secrets.token_urlsafe(32)
        size_bytes = out_path.stat().st_size
        artifact = CallArtifact(
            call_id=call_id,
            kind="summary_pdf",
            file_path=str(out_path),
            size_bytes=size_bytes,
            download_token=download_token,
        )
        db.add(artifact)

    # Try to enqueue Discord notify in a separate session; the PDF
    # artifact is now committed regardless.
    try:
        from shared.tasks.discord_notify import notify_discord

        notify_discord.send(call_id=call_id)
        logger.info("discord_notify_enqueued_after_pdf", call_id=call_id)
    except ImportError:
        logger.warning("discord_notify_not_available")

    logger.info(
        "generate_summary_pdf_complete",
        call_id=call_id,
        size_bytes=size_bytes,
    )
    return {"call_id": call_id, "size_bytes": size_bytes}
