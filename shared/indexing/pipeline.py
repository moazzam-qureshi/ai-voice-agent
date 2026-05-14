"""Document indexing pipeline with hybrid search (BM25 + kNN).

Page-level indexing approach:
- All documents are normalized to page images via PyMuPDF.
- VLM extracts structured content per page: summary + full_content.
- summary is indexed for search retrieval (BM25 + kNN).
- full_content is stored for LLM answering (not indexed).

Embeddings: sentence-transformers/all-MiniLM-L6-v2 (384 dimensions).
"""

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import structlog
from opensearchpy import OpenSearch
from opensearchpy.exceptions import NotFoundError
from sentence_transformers import SentenceTransformer

from shared.indexing.page_parser import PageLevelParser

logger = structlog.get_logger(__name__)

_embedding_model: SentenceTransformer | None = None


def _get_embedding_model() -> SentenceTransformer:
    """Lazy-load the embedding model used for indexing."""
    global _embedding_model
    if _embedding_model is None:
        logger.info("loading_embedding_model_for_indexing", model="all-MiniLM-L6-v2")
        _embedding_model = SentenceTransformer(
            "sentence-transformers/all-MiniLM-L6-v2",
            device="cpu",
        )
        logger.info("embedding_model_loaded_for_indexing")
    return _embedding_model


PAGE_INDEX_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "index": {
            "refresh_interval": "1s",
            "knn": True,
        },
    },
    "mappings": {
        "properties": {
            "document_id": {"type": "keyword"},
            "filename": {"type": "keyword"},
            "file_type": {"type": "keyword"},
            "file_hash": {"type": "keyword"},
            "page_number": {"type": "integer"},
            "summary": {
                "type": "text",
                "analyzer": "english",
            },
            "summary_embedding": {
                "type": "knn_vector",
                "dimension": 384,
                "method": {
                    "engine": "lucene",
                    "space_type": "l2",
                    "name": "hnsw",
                    "parameters": {
                        "ef_construction": 128,
                        "m": 24,
                    },
                },
            },
            "full_content": {
                "type": "text",
                "index": False,  # Stored only, not indexed
            },
            "ingested_at": {"type": "date"},
        }
    },
}


@dataclass
class PageIndexingResult:
    """Result of page-level document indexing."""

    document_id: str
    filename: str
    page_count: int
    file_type: str
    file_hash: str
    success: bool
    error: str | None = None


class PageLevelIndexer:
    """Page-level document indexer over OpenSearch."""

    SUPPORTED_EXTENSIONS = {".pdf"}

    def __init__(
        self,
        opensearch_host: str | None = None,
        opensearch_port: int | None = None,
        index_name: str | None = None,
        openrouter_api_key: str | None = None,
        openrouter_model: str = "qwen/qwen2.5-vl-72b-instruct",
    ):
        self.opensearch_host = opensearch_host or os.getenv("OPENSEARCH_HOST", "localhost")
        self.opensearch_port = int(opensearch_port or os.getenv("OPENSEARCH_PORT", "9200"))
        base_index = index_name or os.getenv("OPENSEARCH_INDEX", "documents")
        self.index_name = f"{base_index}_pages"

        self._parser = PageLevelParser(
            openrouter_api_key=openrouter_api_key or os.getenv("OPENROUTER_API_KEY", ""),
            model=openrouter_model,
        )

        self._client: OpenSearch | None = None

    def _get_client(self) -> OpenSearch:
        if self._client is None:
            self._client = OpenSearch(
                hosts=[{"host": self.opensearch_host, "port": self.opensearch_port}],
                http_compress=True,
                use_ssl=False,
                verify_certs=False,
                ssl_show_warn=False,
            )
            self._ensure_index()
        return self._client

    def _ensure_index(self) -> None:
        if self._client is None:
            return

        if not self._client.indices.exists(index=self.index_name):
            self._client.indices.create(index=self.index_name, body=PAGE_INDEX_MAPPING)
            logger.info("page_index_created", name=self.index_name)
        else:
            logger.info("page_index_exists", name=self.index_name)

        self._ensure_search_pipeline()

    def _ensure_search_pipeline(self) -> None:
        if self._client is None:
            return

        pipeline_id = "page-hybrid-search-pipeline"

        try:
            self._client.transport.perform_request("GET", f"/_search/pipeline/{pipeline_id}")
            logger.info("page_search_pipeline_exists", pipeline_id=pipeline_id)
        except NotFoundError:
            pipeline_body = {
                "description": "Hybrid search for page summaries",
                "phase_results_processors": [
                    {
                        "normalization-processor": {
                            "normalization": {"technique": "min_max"},
                            "combination": {
                                "technique": "arithmetic_mean",
                                "parameters": {"weights": [0.7, 0.3]},
                            },
                        }
                    }
                ],
            }
            self._client.transport.perform_request(
                "PUT", f"/_search/pipeline/{pipeline_id}", body=pipeline_body
            )
            logger.info("page_search_pipeline_created", pipeline_id=pipeline_id)

    def _compute_file_hash(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    async def index_document(
        self,
        content: bytes,
        filename: str,
        document_id: str | None = None,
        delete_existing: bool = True,
        on_total_pages: Callable[[int], None] | None = None,
        on_page_done: Callable[[int, int], None] | None = None,
    ) -> PageIndexingResult:
        """Index a document page-by-page into OpenSearch.

        Optional callbacks are forwarded to the parser so callers can
        observe per-page progress (used by the worker to write
        ProcessingLog rows the UI polls).
        """
        document_id = document_id or str(uuid4())
        file_hash = self._compute_file_hash(content)
        file_ext = os.path.splitext(filename)[1].lower()
        file_type = file_ext.lstrip(".")

        if delete_existing:
            deleted = self.delete_document(document_id)
            if deleted > 0:
                logger.info("existing_pages_deleted", document_id=document_id, count=deleted)

        logger.info(
            "page_level_indexing_start",
            document_id=document_id,
            filename=filename,
            file_type=file_type,
            size_bytes=len(content),
        )

        try:
            result = self._parser.parse(
                content,
                filename,
                on_total_pages=on_total_pages,
                on_page_done=on_page_done,
            )

            if not result.success:
                raise ValueError(result.error or "Parsing failed")

            if not result.pages:
                raise ValueError("No pages extracted from document")

            pages_to_index = [p for p in result.pages if not p.skip]
            skipped_count = len(result.pages) - len(pages_to_index)

            if skipped_count > 0:
                logger.info(
                    "pages_skipped",
                    document_id=document_id,
                    skipped=skipped_count,
                    total=len(result.pages),
                )

            if not pages_to_index:
                logger.warning(
                    "all_pages_skipped",
                    document_id=document_id,
                    total=len(result.pages),
                )
                return PageIndexingResult(
                    document_id=document_id,
                    filename=filename,
                    page_count=0,
                    file_type=file_type,
                    file_hash=file_hash,
                    success=True,
                )

            client = self._get_client()
            embedding_model = _get_embedding_model()
            bulk_body = []

            for page in pages_to_index:
                page_id = f"{document_id}_p{page.page_number}"
                bulk_body.append({"index": {"_index": self.index_name, "_id": page_id}})

                summary_embedding = embedding_model.encode(
                    page.summary,
                    convert_to_tensor=False,
                ).tolist()

                doc = {
                    "document_id": document_id,
                    "filename": filename,
                    "file_type": file_type,
                    "file_hash": file_hash,
                    "page_number": page.page_number,
                    "summary": page.summary,
                    "summary_embedding": summary_embedding,
                    "full_content": page.full_content,
                    "ingested_at": datetime.now(UTC).isoformat(),
                }
                bulk_body.append(doc)

            if bulk_body:
                response = client.bulk(body=bulk_body, refresh=True)
                if response.get("errors"):
                    logger.warning(
                        "page_bulk_index_partial_errors",
                        document_id=document_id,
                        errors=response.get("items", [])[:3],
                    )

            logger.info(
                "page_level_indexing_complete",
                document_id=document_id,
                page_count=len(pages_to_index),
                skipped_count=skipped_count,
            )

            return PageIndexingResult(
                document_id=document_id,
                filename=filename,
                page_count=len(pages_to_index),
                file_type=file_type,
                file_hash=file_hash,
                success=True,
            )

        except Exception as e:
            logger.error("page_level_indexing_failed", document_id=document_id, error=str(e))
            return PageIndexingResult(
                document_id=document_id,
                filename=filename,
                page_count=0,
                file_type=file_type,
                file_hash=file_hash,
                success=False,
                error=str(e),
            )

    def delete_document(self, document_id: str) -> int:
        """Delete all pages for a document. Returns count deleted."""
        client = self._get_client()

        delete_body = {"query": {"term": {"document_id": document_id}}}

        try:
            response = client.delete_by_query(
                index=self.index_name,
                body=delete_body,
                refresh=True,
            )
            deleted = response.get("deleted", 0)
            if deleted > 0:
                logger.info("pages_deleted", document_id=document_id, count=deleted)
            return deleted
        except NotFoundError:
            return 0


_page_indexer_instance: PageLevelIndexer | None = None


def get_page_indexer() -> PageLevelIndexer:
    """Singleton accessor for the page-level indexer."""
    global _page_indexer_instance
    if _page_indexer_instance is None:
        _page_indexer_instance = PageLevelIndexer()
    return _page_indexer_instance
