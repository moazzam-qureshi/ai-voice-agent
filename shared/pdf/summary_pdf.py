"""Render the call-summary PDF using WeasyPrint.

Called from the `generate_summary_pdf` actor with the Call row data
already loaded. Returns the path to the written PDF.

Template lives next to this file. Inter font is installed in the
worker Docker image (fonts-inter package); WeasyPrint resolves it
through fontconfig.
"""

from pathlib import Path
from typing import Any

import structlog
from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = structlog.get_logger(__name__)

_TEMPLATE_DIR = Path(__file__).parent


def _get_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )


def _format_duration(seconds: int | None) -> str:
    if not seconds or seconds <= 0:
        return "—"
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


def render_summary_pdf(
    *,
    call_id: str,
    visitor_name: str | None,
    project_brief: str | None,
    fit_score: str | None,
    fit_reasoning: str | None,
    action_items: list[str] | None,
    duration_seconds: int | None,
    date_iso: str,
    relevant_projects: list[dict[str, Any]] | None,
    out_path: Path,
) -> Path:
    """Render the summary template to a PDF at `out_path`."""
    # Defer the heavy import until call time. WeasyPrint pulls in
    # cairo/pango bindings; we'd rather pay it once per worker process
    # on first PDF, not at module import.
    from weasyprint import HTML

    env = _get_env()
    template = env.get_template("summary_template.html")

    html_str = template.render(
        call_id_short=call_id[:8],
        visitor_name=visitor_name or "—",
        project_brief=project_brief or "—",
        fit_score=(fit_score or "partial").lower(),
        fit_reasoning=fit_reasoning or "—",
        action_items=action_items or [],
        duration=_format_duration(duration_seconds),
        date_iso=date_iso,
        relevant_projects=relevant_projects or [],
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_str, base_url=str(_TEMPLATE_DIR)).write_pdf(str(out_path))

    logger.info(
        "summary_pdf_rendered",
        call_id=call_id,
        path=str(out_path),
        size_bytes=out_path.stat().st_size,
    )
    return out_path
