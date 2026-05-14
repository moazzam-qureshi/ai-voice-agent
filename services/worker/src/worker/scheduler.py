"""Scheduler process — enqueues periodic Dramatiq jobs.

A separate container from the worker. No heavy work itself; calls
`cleanup_expired_calls.send()` once an hour.

Phase 4 wires the actor in. Until then this process boots and idles —
useful for catching configuration issues at deploy time rather than
when the first call expires.
"""

# ruff: noqa: I001, E402

import logging
import signal
import sys

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from worker import broker  # noqa: F401
import shared.tasks  # noqa: F401

logging.basicConfig(stream=sys.stdout, format="%(message)s", level=logging.INFO)
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


def main() -> None:
    scheduler = BlockingScheduler()

    # Phase 4 adds the actor; this is a placeholder so the scheduler boots
    # cleanly today and the contract is visible.
    try:
        from shared.tasks import cleanup_expired_calls  # type: ignore[attr-defined]

        scheduler.add_job(
            cleanup_expired_calls.send,
            IntervalTrigger(hours=1),
            id="cleanup_expired_calls",
            name="Purge expired call data (24h TTL)",
            replace_existing=True,
        )
        logger.info("cleanup_job_registered")
    except ImportError:
        logger.warning("cleanup_actor_not_yet_implemented_scheduler_idle")

    def shutdown(signum, frame):
        logger.info("scheduler_stopping")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("scheduler_started", interval="hourly")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler_stopped")


if __name__ == "__main__":
    main()
