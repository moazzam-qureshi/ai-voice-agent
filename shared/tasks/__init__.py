"""Dramatiq actors shared between API (send side) and worker (run side).

Both services must import their own `broker` module BEFORE importing
`shared.tasks`, since actor decorators register against whatever broker
was last set via `dramatiq.set_broker()`.

Importing this package as a side-effect registers all actors on the
currently-active broker.
"""

from shared.tasks.cleanup import cleanup_expired_calls
from shared.tasks.discord_notify import notify_discord
from shared.tasks.generate_pdf import generate_summary_pdf
from shared.tasks.ingest_knowledge import ingest_knowledge_document

__all__ = [
    "cleanup_expired_calls",
    "notify_discord",
    "generate_summary_pdf",
    "ingest_knowledge_document",
]
