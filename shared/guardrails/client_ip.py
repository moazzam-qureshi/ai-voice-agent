"""Helper for downstream code to read the trusted client IP off the request."""

from starlette.requests import Request


def get_client_ip(request: Request) -> str:
    """Return the trusted client IP set by TrustedProxyMiddleware.

    Falls back to "unknown" if the middleware didn't run (shouldn't happen
    in production but defensive for tests).
    """
    return getattr(request.state, "client_ip", "") or "unknown"
