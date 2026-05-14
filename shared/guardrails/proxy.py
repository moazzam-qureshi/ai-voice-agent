"""Trusted-proxy middleware.

Coolify runs Traefik in front of the app; the real client IP arrives in
`X-Forwarded-For` only when the request comes through Traefik. We trust that
header only if the immediate peer (request.client.host) is on a trusted CIDR.
Otherwise we use the raw peer IP, preventing spoofing.
"""

import ipaddress

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)


def _parse_trusted_cidrs(spec: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse a comma-separated list of CIDRs or single IPs."""
    out: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for raw in spec.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            if "/" not in raw:
                addr = ipaddress.ip_address(raw)
                raw = f"{raw}/{32 if addr.version == 4 else 128}"
            out.append(ipaddress.ip_network(raw, strict=False))
        except ValueError as e:
            logger.warning("trusted_proxy_invalid_cidr", value=raw, error=str(e))
    return out


def _ip_in_trusted(
    ip_str: str,
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in net for net in networks)


class TrustedProxyMiddleware(BaseHTTPMiddleware):
    """Set `request.state.client_ip` to the real client IP, anti-spoof.

    - If the immediate peer is in a trusted-proxy CIDR, take the leftmost
      entry of X-Forwarded-For (the original client).
    - Otherwise, use `request.client.host` directly.

    Downstream handlers should read `request.state.client_ip`, NOT
    `request.client.host`.
    """

    def __init__(self, app, trusted_proxies: str):
        super().__init__(app)
        self.trusted_networks = _parse_trusted_cidrs(trusted_proxies)

    async def dispatch(self, request: Request, call_next) -> Response:
        peer_ip = request.client.host if request.client else ""

        if peer_ip and _ip_in_trusted(peer_ip, self.trusted_networks):
            forwarded = request.headers.get("x-forwarded-for", "").strip()
            if forwarded:
                client_ip = forwarded.split(",")[0].strip()
            else:
                client_ip = peer_ip
        else:
            client_ip = peer_ip

        request.state.client_ip = client_ip
        return await call_next(request)
