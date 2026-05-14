"""Dramatiq actor: ingest a knowledge-base PDF.

Triggered by the admin upload endpoint (POST /admin/knowledge). Reads
the file from /data/knowledge/<doc_id>/<filename>, runs the VLM
extraction pipeline ported from DocuAI, indexes each page into
voicegen_knowledge_pages.

KnowledgeDocument rows are PERMANENT — they don't get an expires_at.
The cleanup actor doesn't touch them. To remove a knowledge document,
call delete via the admin endpoint (not yet implemented; not blocking
the MVP).

This is the slowest actor (Qwen 2.5 VL per page ≈ 5-15 seconds). The
admin upload is fire-and-forget; the admin can poll
GET /admin/knowledge/{id} for status.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import dramatiq
import structlog
from sqlalchemy import select

from shared.db_models import KnowledgeDocument, KnowledgeStatus
from shared.indexing.pipeline import PageLevelIndexer

logger = structlog.get_logger(__name__)


@dramatiq.actor(
    queue_name="knowledge",
    priority=5,
    max_retries=0,  # VLM costs real money; don't auto-retry on failure
    time_limit=30 * 60 * 1000,  # 30 min — knowledge docs may be long
)
def ingest_knowledge_document(knowledge_doc_id: str) -> dict:
    from worker.config import settings as wsettings
    from worker.db.session import get_db_session

    logger.info("ingest_knowledge_start", knowledge_doc_id=knowledge_doc_id)

    with get_db_session() as db:
        doc = db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == knowledge_doc_id)
        ).scalar_one_or_none()
        if doc is None:
            raise ValueError(f"knowledge document not found: {knowledge_doc_id}")

        doc.status = KnowledgeStatus.PROCESSING.value
        # Force a commit so admin polling sees the status flip immediately.
        db.commit()

        file_path = Path(wsettings.data_dir) / "knowledge" / knowledge_doc_id / doc.filename
        if not file_path.exists():
            doc.status = KnowledgeStatus.FAILED.value
            doc.error_message = f"File not found at {file_path}"
            raise FileNotFoundError(str(file_path))

        try:
            content = file_path.read_bytes()

            indexer = PageLevelIndexer(
                opensearch_host=wsettings.opensearch_host,
                opensearch_port=wsettings.opensearch_port,
                index_name=wsettings.opensearch_index,
                openrouter_api_key=wsettings.openrouter_api_key,
                openrouter_model=wsettings.openrouter_vlm_model,
            )

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    indexer.index_document(
                        content=content,
                        filename=doc.filename,
                        document_id=knowledge_doc_id,
                        delete_existing=True,
                    )
                )
            finally:
                loop.close()

            if not result.success:
                raise RuntimeError(result.error or "indexing failed")

            doc.status = KnowledgeStatus.INDEXED.value
            doc.page_count = result.page_count
            doc.error_message = None
            doc.last_indexed_at = datetime.now(UTC)

            logger.info(
                "ingest_knowledge_complete",
                knowledge_doc_id=knowledge_doc_id,
                page_count=result.page_count,
            )
            return {
                "knowledge_doc_id": knowledge_doc_id,
                "status": "indexed",
                "page_count": result.page_count,
            }

        except Exception as e:
            doc.status = KnowledgeStatus.FAILED.value
            doc.error_message = str(e)[:1000]
            logger.error(
                "ingest_knowledge_failed",
                knowledge_doc_id=knowledge_doc_id,
                error=str(e),
            )
            raise
