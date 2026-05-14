"""Call session model + downstream artifacts and transcript messages.

See docs/architecture.md "Data model" section for the schema rationale.
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db_models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    pass


class CallStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    DELETED = "deleted"


class Call(Base, UUIDMixin, TimestampMixin):
    """One voice call session, end to end."""

    __tablename__ = "calls"

    # Origin
    client_ip: Mapped[str | None] = mapped_column(INET, nullable=True, index=True)

    # ElevenLabs side
    elevenlabs_conversation_id: Mapped[str | None] = mapped_column(
        String(128),
        index=True,
        nullable=True,
    )

    # Lifecycle
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=CallStatus.IN_PROGRESS.value,
        nullable=False,
    )

    # Captured by the wrap_up tool
    visitor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    project_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    fit_score: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fit_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_items: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Notification side-effects
    discord_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # 24h TTL — cleanup actor deletes when expires_at < now()
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    # Relationships
    artifacts: Mapped[list["CallArtifact"]] = relationship(
        back_populates="call",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list["CallMessage"]] = relationship(
        back_populates="call",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_calls_client_ip_started", "client_ip", "started_at"),
    )

    def __repr__(self) -> str:
        return f"<Call(id={self.id}, status={self.status}, visitor={self.visitor_name!r})>"


class CallMessage(Base, UUIDMixin):
    """Transcript turn within a call.

    Mostly used as a fallback transcript record (the canonical version comes
    from ElevenLabs' post-call webhook). Also records inline tool-call markers
    so the live UI's transcript pane can replay accurately.
    """

    __tablename__ = "call_messages"

    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # agent | visitor | tool
    content: Mapped[str] = mapped_column(Text, nullable=False)
    ts_offset_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default="now()",
        nullable=False,
    )

    call: Mapped["Call"] = relationship(back_populates="messages")


class CallArtifact(Base, UUIDMixin):
    """A downloadable file produced by a call: summary PDF or recording MP3."""

    __tablename__ = "call_artifacts"

    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # summary_pdf | recording_mp3
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_token: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default="now()",
        nullable=False,
    )

    call: Mapped["Call"] = relationship(back_populates="artifacts")
