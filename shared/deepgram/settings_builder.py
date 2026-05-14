"""Build the Deepgram `Settings` message sent on WebSocket open.

We construct this server-side and ship it to the browser in /call/start
so the system prompt and function definitions never appear in the
client-side bundle.

The browser sends this verbatim once the `Welcome` message arrives.
"""

from typing import Any


def build_agent_settings(
    *,
    system_prompt: str,
    greeting: str,
    stt_model: str,
    llm_provider: str,
    llm_model: str,
    tts_model: str,
) -> dict[str, Any]:
    """Construct the full Settings JSON.

    Function definitions are hardcoded here because there are exactly two
    and they're tightly coupled to our endpoints — there's no win from
    making them configurable. If we add more tools, this is where they
    go.
    """
    return {
        "type": "Settings",
        "audio": {
            "input":  {"encoding": "linear16", "sample_rate": 16000},
            "output": {
                "encoding": "mp3",
                "sample_rate": 24000,
                "bitrate": 48000,
                "container": "none",
            },
        },
        "agent": {
            "language": "en",
            "listen": {
                "provider": {
                    "type": "deepgram",
                    "model": stt_model,
                    "eot_threshold": 0.8,
                }
            },
            "think": {
                "provider": {
                    "type": llm_provider,
                    "model": llm_model,
                    "temperature": 0.6,
                },
                "prompt": system_prompt,
                "context_length": 8000,
                "functions": [
                    {
                        "name": "search_background",
                        "description": (
                            "Search Moazzam's resume and project documentation for "
                            "information relevant to the visitor's project. Use this "
                            "any time you need to describe Moazzam's experience, past "
                            "projects, or capabilities. Always call this before "
                            "making specific claims."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": (
                                        "Natural language query describing what to "
                                        "look up. Examples: 'multi-agent research "
                                        "systems', 'LangGraph experience', 'past RAG "
                                        "projects in healthcare'."
                                    ),
                                },
                                "top_k": {
                                    "type": "integer",
                                    "description": "Max number of passages to return.",
                                    "default": 3,
                                },
                            },
                            "required": ["query"],
                        },
                    },
                    {
                        "name": "wrap_up",
                        "description": (
                            "Call this when you have gathered enough information to "
                            "end the call: the visitor's name, a clear project "
                            "description, a rough timeline, and a fit assessment. "
                            "After this, the call ends gracefully."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "visitor_name": {"type": "string"},
                                "project_brief": {
                                    "type": "string",
                                    "description": (
                                        "The visitor's project in their own words, "
                                        "2-4 sentences."
                                    ),
                                },
                                "fit_score": {
                                    "type": "string",
                                    "enum": ["strong", "partial", "weak"],
                                    "description": (
                                        "Honest assessment of how well Moazzam's "
                                        "experience matches what the visitor needs."
                                    ),
                                },
                                "fit_reasoning": {
                                    "type": "string",
                                    "description": (
                                        "1-2 sentences on why this fit score, with "
                                        "reference to relevant past projects."
                                    ),
                                },
                                "action_items": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Concrete next steps, typically 2-3 items."
                                    ),
                                },
                            },
                            "required": [
                                "visitor_name",
                                "project_brief",
                                "fit_score",
                                "action_items",
                            ],
                        },
                    },
                ],
            },
            "speak": {
                "provider": {
                    "type": "deepgram",
                    "model": tts_model,
                    "speed": 1.0,
                }
            },
            "greeting": greeting,
        },
    }
