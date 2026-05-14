"""OpenSearch store — page-level hybrid retrieval (BM25 + kNN).

Reads side of the indexing pipeline. The worker (Phase 4) writes pages
here when a knowledge document is ingested; /agent/search reads them.

Ported from DocuAI's services/api/src/api/db/opensearch_store.py with
the index name swapped to `voicegen_knowledge_pages`. The hybrid
search pipeline (70/30 BM25 + kNN with min-max normalization) is the
same one DocuAI uses, registered at first index creation.

The same module is imported by the worker cleanup actor (Phase 4) —
that's why it lives in api/db rather than worker/db, and both Dockerfiles
put services/api/src on PYTHONPATH.
"""

from functools import lru_cache
from typing import Any

import structlog
from opensearchpy import OpenSearch
from opensearchpy.exceptions import NotFoundError
from sentence_transformers import SentenceTransformer

from api.config import settings

logger = structlog.get_logger(__name__)

_embedding_model: SentenceTransformer | None = None


def _get_embedding_model() -> SentenceTransformer:
    """Lazy-load the query embedding model. Pre-fetched at image build."""
    global _embedding_model
    if _embedding_model is None:
        logger.info("loading_embedding_model", model="all-MiniLM-L6-v2")
        _embedding_model = SentenceTransformer(
            "sentence-transformers/all-MiniLM-L6-v2",
            device="cpu",
        )
        logger.info("embedding_model_loaded")
    return _embedding_model


class PageSearchStore:
    """Read-side accessor for the knowledge-base page index."""

    def __init__(self) -> None:
        self.host = settings.opensearch_host
        self.port = settings.opensearch_port
        self.index_name = f"{settings.opensearch_index}_pages"
        self._client: OpenSearch | None = None

    def _get_client(self) -> OpenSearch:
        if self._client is None:
            self._client = OpenSearch(
                hosts=[{"host": self.host, "port": self.port}],
                http_compress=True,
                use_ssl=False,
                verify_certs=False,
                ssl_show_warn=False,
            )
            logger.info(
                "page_store_connected",
                host=self.host,
                port=self.port,
                index=self.index_name,
            )
        return self._client

    def hybrid_search(
        self,
        query: str,
        k: int = 3,
    ) -> list[dict[str, Any]]:
        """BM25 + kNN hybrid search on page summaries."""
        client = self._get_client()
        model = _get_embedding_model()

        query_embedding = model.encode(query, convert_to_tensor=False).tolist()

        hybrid_queries = [
            {
                "match": {
                    "summary": {
                        "query": query,
                        "analyzer": "english",
                    }
                }
            },
            {
                "knn": {
                    "summary_embedding": {
                        "vector": query_embedding,
                        "k": k * 2,
                    }
                }
            },
        ]

        search_body: dict[str, Any] = {
            "query": {"hybrid": {"queries": hybrid_queries}},
            "size": k,
            "_source": [
                "document_id",
                "filename",
                "page_number",
                "summary",
                "full_content",
            ],
        }

        try:
            response = client.search(
                index=self.index_name,
                body=search_body,
                params={"search_pipeline": "page-hybrid-search-pipeline"},
            )
        except NotFoundError:
            logger.warning("page_index_not_found", index=self.index_name)
            return []
        except Exception as e:
            logger.error("page_hybrid_search_error", error=str(e))
            return self.bm25_search(query, k)

        return [
            {
                "summary": hit["_source"].get("summary", ""),
                "full_content": hit["_source"].get("full_content", ""),
                "page_number": hit["_source"].get("page_number", 0),
                "document_id": hit["_source"].get("document_id", ""),
                "filename": hit["_source"].get("filename", ""),
                "score": hit.get("_score", 0.0),
            }
            for hit in response["hits"]["hits"]
        ]

    def bm25_search(
        self,
        query: str,
        k: int = 3,
    ) -> list[dict[str, Any]]:
        """BM25-only fallback when hybrid isn't available."""
        client = self._get_client()

        search_body: dict[str, Any] = {
            "query": {
                "match": {
                    "summary": {
                        "query": query,
                        "analyzer": "english",
                    }
                }
            },
            "size": k,
            "_source": [
                "document_id",
                "filename",
                "page_number",
                "summary",
                "full_content",
            ],
        }

        try:
            response = client.search(index=self.index_name, body=search_body)
        except NotFoundError:
            logger.warning("page_index_not_found", index=self.index_name)
            return []

        return [
            {
                "summary": hit["_source"].get("summary", ""),
                "full_content": hit["_source"].get("full_content", ""),
                "page_number": hit["_source"].get("page_number", 0),
                "document_id": hit["_source"].get("document_id", ""),
                "filename": hit["_source"].get("filename", ""),
                "score": hit.get("_score", 0.0),
            }
            for hit in response["hits"]["hits"]
        ]

    def delete_document(self, document_id: str) -> int:
        """Remove all pages for a knowledge document."""
        client = self._get_client()

        try:
            response = client.delete_by_query(
                index=self.index_name,
                body={"query": {"term": {"document_id": document_id}}},
                refresh=True,
            )
            return response.get("deleted", 0)
        except NotFoundError:
            return 0


@lru_cache(maxsize=1)
def get_page_store() -> PageSearchStore:
    """Process-wide singleton accessor."""
    return PageSearchStore()
