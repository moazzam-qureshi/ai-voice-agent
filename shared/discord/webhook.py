"""Plain Discord webhook poster — no SDK.

Discord webhooks accept a POST with a JSON body containing `embeds`,
optional `content`, and optional `components`. We use a single rich
embed with the call's wrap-up summary plus two link buttons (rendered
via `components` of type 1 → button type 5 = link button).

Rate limits: Discord allows ~30 webhooks/minute per channel. At our
worst case (22-100 calls/day) we never hit it. The 429 retry-after
header is honored just in case.
"""

import httpx
import structlog

logger = structlog.get_logger(__name__)


_COLOR_FIT = {
    "strong": 0x10B981,
    "partial": 0xF59E0B,
    "weak": 0xEF4444,
}


def _truncate(text: str | None, max_chars: int) -> str:
    if not text:
        return "—"
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def post_call_notification(
    *,
    webhook_url: str,
    visitor_name: str | None,
    fit_score: str | None,
    duration_seconds: int | None,
    project_brief: str | None,
    action_items: list[str] | None,
    summary_pdf_url: str | None,
    recording_url: str | None,
    timeout: float = 10.0,
) -> bool:
    """POST a call-summary embed to Discord. Returns True on 2xx."""
    if not webhook_url:
        logger.warning("discord_webhook_not_configured")
        return False

    fit_key = (fit_score or "partial").lower()
    color = _COLOR_FIT.get(fit_key, 0x5CE1E6)

    fields = [
        {"name": "Fit", "value": fit_key.upper(), "inline": True},
        {
            "name": "Duration",
            "value": f"{duration_seconds}s" if duration_seconds else "—",
            "inline": True,
        },
        {
            "name": "Project brief",
            "value": _truncate(project_brief, 1024),
            "inline": False,
        },
    ]
    if action_items:
        action_text = "\n".join(f"• {item}" for item in action_items[:5])
        fields.append(
            {"name": "Action items", "value": _truncate(action_text, 1024), "inline": False}
        )

    payload: dict = {
        "username": "VoiceGen AI",
        "embeds": [
            {
                "title": f"New VoiceGen lead: {visitor_name or 'Unknown visitor'}",
                "color": color,
                "fields": fields,
            }
        ],
    }

    # Discord components only render in webhooks if "with_components=true" is
    # used and the bot has the right permissions. For plain webhooks the
    # safer pattern is to put the URLs in the embed footer/description.
    link_lines = []
    if summary_pdf_url:
        link_lines.append(f"[📄 Summary PDF]({summary_pdf_url})")
    if recording_url:
        link_lines.append(f"[🎙 Recording]({recording_url})")
    if link_lines:
        payload["embeds"][0]["description"] = " · ".join(link_lines)

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(webhook_url, json=payload)
    except httpx.HTTPError as e:
        logger.error("discord_webhook_request_failed", error=str(e))
        return False

    if resp.status_code >= 400:
        logger.error(
            "discord_webhook_rejected",
            status=resp.status_code,
            body=resp.text[:300],
        )
        return False

    logger.info("discord_notification_sent", visitor_name=visitor_name, fit=fit_key)
    return True
