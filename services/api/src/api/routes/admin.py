"""Admin endpoints — knowledge base management.

Auth: Bearer token via `Authorization: Bearer <ADMIN_TOKEN>` header.
If ADMIN_TOKEN is empty in env, all admin endpoints return 403 — locked
by default; you have to explicitly opt in by setting the env var.

Endpoints:
- POST  /admin/knowledge          — upload a PDF, enqueue ingest
- GET   /admin/knowledge          — list all knowledge documents + status
- GET   /admin/knowledge/{id}     — single document status

Not in MVP: DELETE /admin/knowledge/{id} (would need to delete OpenSearch
pages too — easy to add later if needed; for now, manual via curl is fine).

The admin endpoints are intentionally bare. The portfolio audience for
this project is engineers, who can use curl. A UI would be over-scope.
"""

import os
import secrets
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.db.session import get_db
from shared.db_models import KnowledgeDocument, KnowledgeStatus

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin")

_KNOWLEDGE_DIR = "knowledge"
_ALLOWED_EXTENSIONS = {".pdf"}


def require_admin(authorization: str = Header(default="")) -> None:
    """FastAPI dep: verify Bearer token against ADMIN_TOKEN."""
    if not settings.admin_token:
        raise HTTPException(status_code=403, detail="admin_endpoints_disabled")
    expected = f"Bearer {settings.admin_token}"
    if not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=403, detail="invalid_admin_token")


class KnowledgeDocumentResponse(BaseModel):
    id: str
    filename: str
    status: str
    page_count: int
    tag: str | None
    error_message: str | None
    last_indexed_at: str | None


def _to_response(doc: KnowledgeDocument) -> KnowledgeDocumentResponse:
    return KnowledgeDocumentResponse(
        id=doc.id,
        filename=doc.filename,
        status=doc.status,
        page_count=doc.page_count,
        tag=doc.tag,
        error_message=doc.error_message,
        last_indexed_at=doc.last_indexed_at.isoformat() if doc.last_indexed_at else None,
    )


@router.post("/knowledge", response_model=KnowledgeDocumentResponse)
async def upload_knowledge(
    file: UploadFile = File(...),
    tag: str = Form(default=""),
    _: None = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDocumentResponse:
    """Upload a knowledge-base PDF and enqueue VLM ingest.

    Returns immediately with `status='pending'`. Poll
    `GET /admin/knowledge/{id}` until status becomes `indexed` or `failed`.
    """
    suffix = os.path.splitext(file.filename or "")[1].lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported_file_type: {suffix}. allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty_file")

    doc = KnowledgeDocument(
        filename=file.filename or "knowledge.pdf",
        status=KnowledgeStatus.PENDING.value,
        tag=tag or None,
        metadata_={"content_type": file.content_type or ""},
    )
    db.add(doc)
    await db.flush()
    doc_id = doc.id

    # Write file to disk for the worker to pick up. /data/knowledge/<id>/<filename>
    out_dir = Path(settings.data_dir) / _KNOWLEDGE_DIR / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / doc.filename
    out_path.write_bytes(content)

    # Enqueue the ingest actor
    try:
        from shared.tasks import ingest_knowledge_document  # type: ignore[attr-defined]

        ingest_knowledge_document.send(knowledge_doc_id=doc_id)
        logger.info(
            "knowledge_upload_enqueued",
            knowledge_doc_id=doc_id,
            filename=doc.filename,
            size_bytes=len(content),
        )
    except (ImportError, AttributeError):
        logger.error("ingest_actor_unavailable_at_admin_upload", knowledge_doc_id=doc_id)
        raise HTTPException(status_code=500, detail="ingest_worker_not_configured")

    return _to_response(doc)


@router.get("/knowledge", response_model=list[KnowledgeDocumentResponse])
async def list_knowledge(
    _: None = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[KnowledgeDocumentResponse]:
    result = await db.execute(
        select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc())
    )
    docs = result.scalars().all()
    return [_to_response(d) for d in docs]


@router.get("/knowledge/{doc_id}", response_model=KnowledgeDocumentResponse)
async def get_knowledge(
    doc_id: str,
    _: None = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDocumentResponse:
    result = await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id)
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="knowledge_document_not_found")
    return _to_response(doc)
