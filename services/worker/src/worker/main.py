"""Worker entry-point — imported by `dramatiq worker.main`.

Order matters:
1. worker.broker must run FIRST so dramatiq.set_broker() executes before any
   actor decoration.
2. shared.tasks imports the actor modules, which register against the broker.

Phase 4 fills in the actors. For now the worker boots with zero actors;
that's fine — Dramatiq won't fail to start.
"""

# ruff: noqa: I001, E402

import logging
import sys

import structlog

# 1. Set the broker BEFORE importing actors.
from worker import broker  # noqa: F401

# 2. Import actors (Phase 4 will populate shared.tasks).
import shared.tasks  # noqa: F401

# 3. Structured logging.
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

structlog.get_logger(__name__).info("voicegen_worker_module_loaded")
