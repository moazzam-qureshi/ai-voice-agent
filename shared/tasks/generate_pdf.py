"""Dramatiq actor: generate the call's summary PDF.

Triggered by /agent/wrap-up or by the time-cap fallback. Regardless of
how it was triggered, this actor runs a real LLM synthesis pass over
the persisted transcript (call_messages) and uses THAT as the source
of truth for the PDF — not the lazy wrap_up arguments the agent gave
us. Cost is ~$0.001 per PDF (gpt-4o-mini), worth it for a polished
deliverable that doesn't look like canned heuristics.

The actor:
1. Loads the Call row (kept as fallback if synthesis fails)
2. Loads the full conversation transcript from call_messages
3. Calls shared.pdf.synthesize to produce a clean structured summary
4. Renders the WeasyPrint template with the synthesized result
5. Inserts a CallArtifact row with a fresh download_token
6. Enqueues notify_discord
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
    from shared.pdf.synthesize import synthesize_summary
    from worker.config import settings as wsettings
    from worker.db.session import get_db_session

    logger.info("generate_summary_pdf_start", call_id=call_id)

    with get_db_session() as db:
        call = db.execute(select(Call).where(Call.id == call_id)).scalar_one_or_none()
        if call is None:
            raise ValueError(f"call not found: {call_id}")

        # Idempotent: skip if a PDF already exists for this call.
        existing = db.execute(
            select(CallArtifact)
            .where(CallArtifact.call_id == call_id)
            .where(CallArtifact.kind == "summary_pdf")
        ).scalar_one_or_none()
        if existing is not None:
            logger.info("summary_pdf_already_exists", call_id=call_id)
            return {"call_id": call_id, "already_exists": True}

        # Load the conversation. We pull both agent/visitor turns AND tool
        # markers so the LLM sees what the agent searched for.
        turns_rows = (
            db.execute(
                select(CallMessage)
                .where(CallMessage.call_id == call_id)
                .order_by(CallMessage.ts_offset_ms.asc(), CallMessage.created_at.asc())
            )
            .scalars()
            .all()
        )
        transcript_turns = [
            {"role": t.role, "content": t.content} for t in turns_rows
        ]

        # Synthesize a real summary from the transcript.
        synthesis = synthesize_summary(
            transcript_turns=transcript_turns,
            openrouter_api_key=wsettings.openrouter_api_key,
            openrouter_base_url=wsettings.openrouter_base_url,
            # Use a chat model, NOT the VLM model. OpenRouter routes both
            # under the same API key.
            openrouter_model="openai/gpt-4o-mini",
            # Keep the agent's wrap_up data as a last-resort fallback.
            fallback_wrap_up={
                "visitor_name": call.visitor_name,
                "project_brief": call.project_brief,
                "fit_score": call.fit_score,
                "fit_reasoning": call.fit_reasoning,
                "action_items": call.action_items,
            },
        )

        logger.info(
            "synthesis_complete",
            call_id=call_id,
            visitor_name=synthesis.visitor_name[:40],
            fit_score=synthesis.fit_score,
            project_brief_len=len(synthesis.project_brief),
            action_items_count=len(synthesis.action_items),
            relevant_projects_count=len(synthesis.relevant_projects),
        )

        out_dir = Path(wsettings.data_dir) / "calls" / call_id
        out_path = out_dir / "summary.pdf"

        date_iso = (
            call.started_at.astimezone(UTC).strftime("%Y-%m-%d")
            if call.started_at
            else ""
        )

        render_summary_pdf(
            call_id=call_id,
            visitor_name=synthesis.visitor_name or "Caller",
            project_brief=synthesis.project_brief,
            fit_score=synthesis.fit_score,
            fit_reasoning=synthesis.fit_reasoning,
            action_items=synthesis.action_items,
            duration_seconds=call.duration_seconds,
            date_iso=date_iso,
            relevant_projects=synthesis.relevant_projects,
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
