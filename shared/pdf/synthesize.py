"""LLM synthesis of the call summary.

Runs in the worker process at PDF generation time. Reads the full
persisted transcript (call_messages) for a call, asks an LLM to produce
a structured summary (visitor name, project brief, fit assessment,
reasoning, action items), and returns a SynthesisResult the PDF
template can render directly.

This is the layer that turns "agent forgot to call wrap_up cleanly" or
"wrap_up arguments were lazy" into "the PDF is still a real, useful
deliverable". Even when wrap_up did fire properly we still re-synthesize
from the transcript because the agent's wrap_up arguments are typically
sparser than what a fresh-eye LLM pass produces.

Cost: one gpt-4o-mini call per PDF (~$0.001). Cheap enough to do
unconditionally.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
import structlog
from openai import OpenAI

logger = structlog.get_logger(__name__)


@dataclass
class SynthesisResult:
    visitor_name: str
    project_brief: str
    fit_score: str  # 'strong' | 'partial' | 'weak'
    fit_reasoning: str
    action_items: list[str]
    relevant_projects: list[dict[str, str]]


_SYSTEM = """\
You write structured call summaries for a freelancing voice agent.
The agent qualifies inbound leads for Moazzam Qureshi, a senior AI engineer.

You are given the FULL transcript of the conversation. Produce a clean,
specific summary in JSON. Do NOT invent details the transcript doesn't
support. If something wasn't covered, return an empty string or empty
list for that field — never fabricate.

Output schema (strict JSON, no markdown fences):
{
  "visitor_name": string,    // exact name the visitor gave, or "" if not asked
  "project_brief": string,   // 2-4 sentences in the visitor's voice/scope.
                             // MUST include the timeline and budget if
                             // they were mentioned, verbatim.
  "fit_score": "strong" | "partial" | "weak",
                             // strong: agent found a clearly relevant past project
                             // partial: adjacent experience but not exact
                             // weak: nothing in Moazzam's portfolio aligns
  "fit_reasoning": string,   // 1-2 sentences referencing the specific past
                             // project the agent quoted. If the agent didn't
                             // get to a fit assessment, say so plainly and
                             // note what's in the transcript that suggests fit.
  "action_items": [string],  // 1-3 concrete next steps for Moazzam.
                             // Always include: "Moazzam will respond within
                             // his usual response window."
  "relevant_projects": [
    { "name": string, "source": string }
  ]                          // Past projects the agent referenced. Empty
                             // list if none. `name` is what was discussed,
                             // `source` is the document/file if mentioned.
}

Style:
- Specific over general. Reference real details the visitor and agent
  said. Don't pad with platitudes.
- If the call was truncated (time cap hit before wrap_up), say so once
  in fit_reasoning, then summarize what WAS covered.
- Use the visitor's exact wording where it captures their need best.
"""


def synthesize_summary(
    *,
    transcript_turns: list[dict[str, str]],
    openrouter_api_key: str,
    openrouter_base_url: str,
    openrouter_model: str,
    fallback_wrap_up: dict | None = None,
) -> SynthesisResult:
    """Run an LLM pass on the transcript.

    Args:
        transcript_turns: list of {role, content} dicts in conversation order.
        openrouter_api_key: same key the VLM ingest uses.
        openrouter_base_url: typically https://openrouter.ai/api/v1.
        openrouter_model: chat model id (e.g. "openai/gpt-4o-mini").
        fallback_wrap_up: if the agent did call wrap_up, those arguments —
            used as a backstop if the LLM call fails entirely.

    Returns:
        SynthesisResult — never raises. On total failure falls back to
        the wrap_up data, or to honest placeholders.
    """
    transcript_text = _format_transcript(transcript_turns)

    if not openrouter_api_key:
        logger.warning("openrouter_key_missing_skipping_synthesis")
        return _from_fallback(fallback_wrap_up)

    if not transcript_text.strip():
        logger.warning("transcript_empty_skipping_synthesis")
        return _from_fallback(fallback_wrap_up)

    try:
        client = OpenAI(
            api_key=openrouter_api_key,
            base_url=openrouter_base_url,
            # Avoid pulling in OpenAI's default httpx config (which can
            # hold sockets in async contexts the worker doesn't run).
            http_client=httpx.Client(timeout=30.0),
        )
        resp = client.chat.completions.create(
            model=openrouter_model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "Here is the full transcript. Produce the JSON summary.\n\n"
                        f"<transcript>\n{transcript_text}\n</transcript>"
                    ),
                },
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
            max_tokens=900,
        )
        raw = resp.choices[0].message.content or "{}"
    except Exception as e:
        logger.error("synthesis_llm_call_failed", error=str(e))
        return _from_fallback(fallback_wrap_up)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("synthesis_json_parse_failed", error=str(e), raw=raw[:200])
        return _from_fallback(fallback_wrap_up)

    return _coerce_result(data, fallback_wrap_up)


# === Helpers ===============================================================


_ROLE_LABEL = {"agent": "AGENT", "visitor": "VISITOR", "tool": "TOOL"}


def _format_transcript(turns: list[dict[str, str]]) -> str:
    """Render the transcript as a clean human-readable string for the LLM."""
    lines = []
    for t in turns:
        role = t.get("role", "")
        content = (t.get("content") or "").strip()
        if not content:
            continue
        label = _ROLE_LABEL.get(role, role.upper())
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


def _coerce_result(
    data: dict,
    fallback_wrap_up: dict | None,
) -> SynthesisResult:
    """Coerce a parsed LLM response into a SynthesisResult, with sensible
    fallbacks for missing or malformed fields."""

    def _str(key: str, default: str = "") -> str:
        v = data.get(key, default)
        return str(v) if v is not None else default

    fit_raw = _str("fit_score", "partial").lower()
    if fit_raw not in {"strong", "partial", "weak"}:
        fit_raw = "partial"

    action_items = data.get("action_items") or []
    if not isinstance(action_items, list):
        action_items = []
    action_items = [str(x) for x in action_items if x][:3]
    if not action_items and fallback_wrap_up and isinstance(
        fallback_wrap_up.get("action_items"), list
    ):
        action_items = [
            str(x) for x in fallback_wrap_up["action_items"] if x
        ][:3]
    if not action_items:
        action_items = [
            "Moazzam will respond within his usual response window."
        ]

    relevant_projects_raw = data.get("relevant_projects") or []
    relevant_projects: list[dict[str, str]] = []
    if isinstance(relevant_projects_raw, list):
        for p in relevant_projects_raw[:5]:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name") or "").strip()
            if not name:
                continue
            relevant_projects.append(
                {"name": name, "source": str(p.get("source") or "")}
            )

    return SynthesisResult(
        visitor_name=_str("visitor_name") or _fallback_str(
            fallback_wrap_up, "visitor_name", ""
        ),
        project_brief=_str("project_brief") or _fallback_str(
            fallback_wrap_up, "project_brief", ""
        ),
        fit_score=fit_raw,
        fit_reasoning=_str("fit_reasoning") or _fallback_str(
            fallback_wrap_up, "fit_reasoning", ""
        ),
        action_items=action_items,
        relevant_projects=relevant_projects,
    )


def _fallback_str(fb: dict | None, key: str, default: str) -> str:
    if not fb:
        return default
    v = fb.get(key)
    return str(v) if v else default


def _from_fallback(fb: dict | None) -> SynthesisResult:
    if not fb:
        return SynthesisResult(
            visitor_name="",
            project_brief="(Call ended before a summary could be produced.)",
            fit_score="partial",
            fit_reasoning="No transcript was available for synthesis.",
            action_items=[
                "Moazzam will respond within his usual response window.",
            ],
            relevant_projects=[],
        )
    fit_raw = str(fb.get("fit_score") or "partial").lower()
    if fit_raw not in {"strong", "partial", "weak"}:
        fit_raw = "partial"
    return SynthesisResult(
        visitor_name=str(fb.get("visitor_name") or ""),
        project_brief=str(fb.get("project_brief") or ""),
        fit_score=fit_raw,
        fit_reasoning=str(fb.get("fit_reasoning") or ""),
        action_items=[str(x) for x in (fb.get("action_items") or []) if x][:3],
        relevant_projects=[],
    )
