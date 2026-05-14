"""Knowledge document model — Moazzam's resume + per-project PDFs.

These are permanent (NOT auto-deleted at 24h like calls). They're uploaded
once via an admin endpoint and indexed into OpenSearch via the same
vision-LLM extraction pipeline as DocuAI.
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.db_models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    pass


class KnowledgeStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class KnowledgeDocument(Base, UUIDMixin, TimestampMixin):
    """An uploaded knowledge-base document (resume, project PDF, etc)."""

    __tablename__ = "knowledge_documents"

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=KnowledgeStatus.PENDING.value,
        nullable=False,
    )
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Free-text tag like "resume", "project:docuai", "project:voicegen"
    # The agent doesn't filter on this, but it's useful for admin debugging.
    tag: Mapped[str | None] = mapped_column(String(128), nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    last_indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
