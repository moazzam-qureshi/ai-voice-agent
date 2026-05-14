"""Sync SQLAlchemy session for the worker process.

Dramatiq actors run sync code by default; using a sync session keeps actor
bodies simple. The API uses async sessions.

The DATABASE_URL env var is the API's async URL (postgresql+asyncpg://...);
we translate it to the sync driver here.
"""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from worker.config import settings


def _sync_url(url: str) -> str:
    """Translate an async URL to its sync equivalent."""
    return url.replace("+asyncpg", "+psycopg2")


engine = create_engine(
    _sync_url(settings.database_url),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

session_factory = sessionmaker(
    engine,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Context-managed sync session with automatic commit/rollback."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
