"""Dramatiq actors shared between API (send side) and worker (run side).

Both services must import their own `broker` module BEFORE importing
`shared.tasks`, since actor decorators register against whatever broker
was last set via `dramatiq.set_broker()`.

Phase 4 fills in the actor implementations. For now this file exposes
the actor names so the API can `.send()` them once they exist.
"""

# Actors are imported here once they're implemented in Phase 4:
# from shared.tasks.generate_pdf import generate_summary_pdf
# from shared.tasks.discord_notify import notify_discord
# from shared.tasks.cleanup import cleanup_expired_calls
# from shared.tasks.ingest_knowledge import ingest_knowledge_document

__all__: list[str] = []
