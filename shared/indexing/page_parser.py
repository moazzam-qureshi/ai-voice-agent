"""Page-level document parser using a vision LLM via Instructor.

Pipeline per page:
1. PyMuPDF renders the page as an image.
2. Vision LLM (Qwen 2.5 VL 72B by default, via OpenRouter) extracts:
   - skip: whether to skip this page (cover/title/blank)
   - summary: short retrieval description
   - full_content: exhaustive verbatim markdown for answering
3. Instructor handles structured output validation + retries.
"""

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import instructor
import structlog
from openai import OpenAI
from pydantic import BaseModel, Field

from .page_extractor import SUPPORTED_EXTENSIONS, extract_pages_as_images, get_page_count

logger = structlog.get_logger(__name__)


class PageContent(BaseModel):
    """Structured content extracted from a single page."""

    skip: bool = Field(
        description=(
            "Set to true for cover pages, title pages, blank pages, or pages with only "
            "logos/branding and no substantive content worth indexing."
        )
    )
    summary: str = Field(
        description=(
            "2-3 sentences describing the page's main topics, key entities (company names, "
            "product names, people), and purpose (e.g. 'product specifications', 'pricing table'). "
            "Used for search retrieval."
        )
    )
    full_content: str = Field(
        description=(
            "Comprehensive verbatim markdown of everything on the page: text, headings, "
            "paragraphs, bullets, captions, footnotes, image text, tables (as markdown), "
            "chart data points, specs with numbers and codes, contact info, dates, entities. "
            "The answering system will ONLY see this text — be exhaustive."
        )
    )


PAGE_VLM_SYSTEM_PROMPT = """You are a document analysis expert. Extract three fields from document pages:

1. "skip": Set to true for cover pages, title pages, blank pages, or pages with only decorative content (logos, branding, images) and no substantive text worth indexing. Set to false for pages with actual content.

2. "summary": 2-3 sentences describing the page's main topics, key entities, and purpose. If skip=true, just write "Cover page" or similar brief description.

3. "full_content":
   - If skip=true: Write only "Cover page" or "Title page" (nothing else, no newlines)
   - If skip=false: VERBATIM text extraction in markdown format:
     - Copy text EXACTLY as written — do not paraphrase or summarize
     - Product codes/model numbers as markdown headings (# or ##)
     - Use markdown tables for tabular data (preserve all rows/columns)
     - Preserve exact wording, numbers, units, and punctuation
     - For bullet points, copy each item word-for-word
     - For specs/dimensions, include exact values with units
     - Extract text from images, logos, diagrams, and captions
     - Include page numbers, headers, footers, and website URLs"""


@dataclass
class PageParseResult:
    """Result of parsing a single page."""

    page_number: int
    summary: str
    full_content: str
    skip: bool = False


@dataclass
class DocumentPageResult:
    """Result of page-level document parsing."""

    pages: list[PageParseResult] = field(default_factory=list)
    page_count: int = 0
    file_type: str = ""
    success: bool = False
    error: str | None = None


class PageLevelParser:
    """Vision-LLM-based page-level parser with Instructor for structured output."""

    def __init__(
        self,
        openrouter_api_key: str | None = None,
        model: str = "qwen/qwen2.5-vl-72b-instruct",
        timeout: float = 300.0,
        dpi: int = 200,
        max_retries: int = 3,
    ):
        self.api_key = openrouter_api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.model = model
        self.timeout = timeout
        self.dpi = dpi
        self.max_retries = max_retries
        self._client = None

    def _get_client(self) -> instructor.Instructor:
        if self._client is None:
            openai_client = OpenAI(
                api_key=self.api_key,
                base_url="https://openrouter.ai/api/v1",
                timeout=self.timeout,
            )
            self._client = instructor.from_openai(openai_client, mode=instructor.Mode.JSON)
        return self._client

    def _call_vlm(self, image_base64: str) -> PageContent:
        client = self._get_client()

        messages = [
            {"role": "system", "content": PAGE_VLM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Analyze this document page and extract the content as JSON.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_base64}",
                        },
                    },
                ],
            },
        ]

        result = client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_model=PageContent,
            max_retries=self.max_retries,
            temperature=0.1,
            max_tokens=16000,
        )
        return result

    def parse_pages(
        self,
        content: bytes,
        filename: str,
        on_total_pages: Callable[[int], None] | None = None,
        on_page_done: Callable[[int, int], None] | None = None,
    ) -> Iterator[PageParseResult]:
        """Parse a document and yield results page-by-page.

        Optional callbacks let callers (e.g. the worker task) surface
        progress to a database / UI without needing to know about the
        generator's internals.
        """
        suffix = Path(filename).suffix.lower()

        if suffix not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {suffix}")

        if not self.api_key:
            raise ValueError("OpenRouter API key is required for page-level parsing")

        # Determine total up front so callbacks can render "n / total" progress.
        # get_page_count is cheap (just opens the PDF, no rendering).
        total_pages = get_page_count(file_content=content, filename=filename)

        logger.info(
            "page_level_parsing_start",
            filename=filename,
            model=self.model,
            total_pages=total_pages,
        )

        if on_total_pages is not None:
            try:
                on_total_pages(total_pages)
            except Exception as cb_e:
                logger.warning("on_total_pages_callback_failed", error=str(cb_e))

        for page_image in extract_pages_as_images(
            file_content=content,
            filename=filename,
            dpi=self.dpi,
        ):
            logger.info(
                "processing_page",
                page_number=page_image.page_number,
                total_pages=total_pages,
                dimensions=f"{page_image.width}x{page_image.height}",
            )

            try:
                page_content = self._call_vlm(page_image.base64_string)
                yield PageParseResult(
                    page_number=page_image.page_number,
                    summary=page_content.summary,
                    full_content=page_content.full_content,
                    skip=page_content.skip,
                )

            except Exception as e:
                logger.error(
                    "page_vlm_error",
                    page_number=page_image.page_number,
                    error=str(e),
                )
                yield PageParseResult(
                    page_number=page_image.page_number,
                    summary=f"Error processing page {page_image.page_number}",
                    full_content=f"Error: {e!s}",
                )

            if on_page_done is not None:
                try:
                    on_page_done(page_image.page_number, total_pages)
                except Exception as cb_e:
                    logger.warning(
                        "on_page_done_callback_failed",
                        page_number=page_image.page_number,
                        error=str(cb_e),
                    )

    def parse(
        self,
        content: bytes,
        filename: str,
        on_total_pages: Callable[[int], None] | None = None,
        on_page_done: Callable[[int, int], None] | None = None,
    ) -> DocumentPageResult:
        """Parse a document and return all pages."""
        suffix = Path(filename).suffix.lower()
        file_type = suffix.lstrip(".")

        try:
            pages = list(
                self.parse_pages(
                    content,
                    filename,
                    on_total_pages=on_total_pages,
                    on_page_done=on_page_done,
                )
            )

            logger.info(
                "page_level_parsing_complete",
                filename=filename,
                page_count=len(pages),
            )

            return DocumentPageResult(
                pages=pages,
                page_count=len(pages),
                file_type=file_type,
                success=True,
            )

        except Exception as e:
            logger.error("page_level_parsing_failed", filename=filename, error=str(e))
            return DocumentPageResult(
                success=False,
                error=str(e),
                file_type=file_type,
            )

    def close(self) -> None:
        self._client = None

    def __enter__(self) -> "PageLevelParser":
        return self

    def __exit__(self, *args) -> None:
        self.close()
