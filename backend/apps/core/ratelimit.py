"""
apps/core/ratelimit.py

Rate-limiting helpers shared across apps (django-ratelimit). See the auth
endpoints wired in apps/accounts/urls.py.

`client_ip` is a django-ratelimit key callable (signature ``(group, request)``)
that resolves the real client IP using the rightmost-untrusted algorithm:

1. If ``REMOTE_ADDR`` is NOT in the trusted-proxy set, the connection arrived
   directly (the edge proxy was bypassed). Use ``REMOTE_ADDR`` and ignore any
   ``X-Forwarded-For`` header — it is attacker-controlled in this path.

2. If ``REMOTE_ADDR`` IS in the trusted-proxy set, walk ``X-Forwarded-For``
   right-to-left and return the first IP that is not itself a trusted proxy.
   This is the real client address as seen by the closest trusted hop.

Trusted-proxy set: RFC-1918 private ranges + loopback. This is correct for both
of this project's deployment shapes:
  * Home server — the app sits behind nginx on the same host, so ``REMOTE_ADDR``
    is loopback (127.0.0.1, trusted) and nginx appends the real client to
    ``X-Forwarded-For`` (rightmost) via ``proxy_params``.
  * Render / Railway — the container's ``REMOTE_ADDR`` is a private platform
    address (trusted), so the XFF path is taken.
A client that reaches the app directly (proxy bypassed) arrives with a public
``REMOTE_ADDR``; their spoofed XFF header is discarded and they are bucketed
under their own IP.
"""

import ipaddress
import json

from django.conf import settings


# ── Central rate-limit configuration ─────────────────────────────────────────
# Every throttled endpoint reads its rate from here so all limits live in one
# place. The values come from settings.RATE_LIMITS, sourced from RATE_LIMIT_*
# env vars (config/settings.py). Format is "count/period" (period: s, m, h, d,
# or a multiple like "15m"). The *key* a limit counts against (IP, IP+email) is
# set on the decorator at the URL, not here.
RATE_LIMITS = settings.RATE_LIMITS


# ── Trusted-proxy CIDR list ───────────────────────────────────────────────────
# Connections arriving from these ranges are treated as proxy hops; every other
# address is treated as a direct (untrusted) client. RFC-1918 private ranges
# cover all PaaS platforms (Railway, Render, Fly.io, …) and the home-server LAN.
_TRUSTED_NETWORKS: "list[ipaddress.IPv4Network | ipaddress.IPv6Network]" = [
    ipaddress.ip_network("127.0.0.0/8"),     # loopback
    ipaddress.ip_network("::1/128"),         # IPv6 loopback
    ipaddress.ip_network("10.0.0.0/8"),      # RFC-1918
    ipaddress.ip_network("172.16.0.0/12"),   # RFC-1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC-1918 (incl. the home LAN)
    ipaddress.ip_network("fc00::/7"),        # IPv6 ULA (private)
]


def _is_trusted_proxy(ip_str: str) -> bool:
    """Return True if ip_str falls within a trusted proxy range."""
    try:
        addr = ipaddress.ip_address(ip_str.strip())
        return any(addr in net for net in _TRUSTED_NETWORKS)
    except ValueError:
        return False


# ── Key callables ─────────────────────────────────────────────────────────────

def client_ip(group, request):
    """
    Rightmost-untrusted client IP for django-ratelimit.

    If REMOTE_ADDR is a trusted proxy (private/loopback range), inspect
    X-Forwarded-For right-to-left and return the first address not in the
    trusted set. If REMOTE_ADDR is a public IP the edge proxy was bypassed —
    return REMOTE_ADDR directly so the attacker's spoofed X-Forwarded-For
    header has no effect on rate-limit bucketing.
    """
    remote = request.META.get("REMOTE_ADDR", "")

    if not _is_trusted_proxy(remote):
        # Direct connection from an untrusted address — proxy was bypassed.
        # Ignore X-Forwarded-For; it is fully attacker-controlled here.
        return remote

    # Behind a trusted proxy: find the rightmost untrusted IP in XFF.
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        for candidate in reversed([ip.strip() for ip in xff.split(",")]):
            if not _is_trusted_proxy(candidate):
                return candidate

    # XFF absent or every entry was a trusted proxy — fall back to REMOTE_ADDR.
    return remote


def ip_and_email(group, request):
    """
    Composite key for password-reset: rate-limits per (IP, email) pair so a
    single IP cannot enumerate accounts by hammering many emails, and a single
    email cannot be flooded from one IP. Falls back to IP-only when the request
    body has no parseable email.

    Note: as a django-ratelimit key callable, this runs with the limiter placed
    outermost (wrapping ``.as_view()`` at the URL), so it receives the raw
    Django request — DRF's ``request.data`` is not yet available. We parse
    ``request.body`` directly; DRF re-parses the buffered body downstream, so
    reading it here is safe.
    """
    ip = client_ip(group, request)
    email = ""
    try:
        body = json.loads(request.body or b"{}")
        email = (body.get("email") or "").lower().strip()
    except (ValueError, AttributeError):
        pass
    return f"{ip}:{email}"
