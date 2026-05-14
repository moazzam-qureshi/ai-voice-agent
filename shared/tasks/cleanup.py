"""Dramatiq actor: hourly cleanup of expired call data.

Runs on the schedule defined by services/worker/src/worker/scheduler.py
(every 1 hour) — finds Call rows past their expires_at, deletes the
files on disk under /data/calls/<id>/, and marks the row status=deleted.

Knowledge documents are NOT touched here — they're permanent until
explicitly removed via the admin endpoint.

Idempotent: a deleted row is identified by status='deleted' and skipped
on subsequent runs. If a file is already missing, we don't fail.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import dramatiq
import structlog
from sqlalchemy import select

from shared.db_models import Call, CallStatus

logger = structlog.get_logger(__name__)


@dramatiq.actor(
    queue_name="cleanup",
    priority=20,
    max_retries=1,
    time_limit=5 * 60 * 1000,  # 5 min
)
def cleanup_expired_calls() -> dict:
    """Find expired calls, delete their data dir + DB rows."""
    from worker.config import settings as wsettings
    from worker.db.session import get_db_session

    now = datetime.now(UTC)
    calls_marked = 0
    dirs_removed = 0
    dirs_missing = 0

    with get_db_session() as db:
        expired = db.execute(
            select(Call)
            .where(Call.expires_at < now)
            .where(Call.status != CallStatus.DELETED.value)
        ).scalars().all()

        for call in expired:
            call_dir = Path(wsettings.data_dir) / "calls" / call.id
            if call_dir.exists():
                try:
                    shutil.rmtree(call_dir)
                    dirs_removed += 1
                except Exception as e:
                    logger.error(
                        "cleanup_dir_removal_failed",
                        call_id=call.id,
                        path=str(call_dir),
                        error=str(e),
                    )
                    # Continue: still mark deleted so we don't keep retrying.
            else:
                dirs_missing += 1

            call.status = CallStatus.DELETED.value
            calls_marked += 1

    logger.info(
        "cleanup_complete",
        calls_marked=calls_marked,
        dirs_removed=dirs_removed,
        dirs_missing=dirs_missing,
    )
    return {
        "calls_marked": calls_marked,
        "dirs_removed": dirs_removed,
        "dirs_missing": dirs_missing,
    }
