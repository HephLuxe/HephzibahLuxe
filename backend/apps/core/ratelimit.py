"""
apps/core/ratelimit.py

**The single source of truth for "who is the client".**

Four things need to answer that question and they must never disagree:

1. django-ratelimit's key callables (``client_ip``, ``ip_and_email``) — the
   per-endpoint limits wired at the URL in apps/accounts/urls.py and
   apps/inquiries/urls.py — plus ``email_key_for``, the same idea for a caller
   that already holds a parsed email (apps/accounts/login_guard.py).
2. DRF's throttles, via ``apps.core.throttling`` (its own ``get_ident`` is
   spoofable — see that module).
3. django-ratelimit's built-in ``key='ip'`` path, via
   ``settings.RATELIMIT_IP_META_KEY``, which is pointed at
   ``resolve_client_ip`` so a future ``@ratelimit(key='ip')`` can't fall back to
   bare ``REMOTE_ADDR``.
4. The password-reset audit trail (``PasswordResetToken.ip_address``).
5. reCAPTCHA verification — ``apps/inquiries/recaptcha.py`` sends ``remoteip``
   to Google's siteverify, resolved here by the view rather than read off the
   request, so the address Google scores is the one the limits bucket on.

Nothing else in this codebase may read ``REMOTE_ADDR`` or ``X-Forwarded-For``.
Before this module owned all four, three separate implementations existed and
two of them were wrong — see docs/RATE_LIMITING_AUDIT.md (D3–D10, P6, P7).

Two public entry points, because the consumers want subtly different things:

``resolve_client_ip(request)``
    The **exact** client address. Used for the audit trail, where precision is
    the point, and as the base for everything below.

``bucket_ip(request)``
    ``resolve_client_ip`` masked to a prefix (IPv4 /32, IPv6 /64). Used for
    every rate-limit bucket. An IPv6 client typically owns a whole /64, so an
    unmasked key lets them pick a fresh source address per request and defeat
    any IP-keyed limit — this is the one place that is prevented.


The resolution algorithm (rightmost-untrusted)
----------------------------------------------
1. If ``REMOTE_ADDR`` is NOT in the trusted-proxy set, the connection arrived
   directly (the edge proxy was bypassed). Use ``REMOTE_ADDR`` and ignore any
   ``X-Forwarded-For`` header — it is attacker-controlled in this path.

2. If ``REMOTE_ADDR`` IS in the trusted-proxy set, walk ``X-Forwarded-For``
   right-to-left and return the first entry that parses as an IP address and is
   not itself a trusted proxy. This is the real client address as seen by the
   closest trusted hop.

Reading right-to-left is what makes the header safe to use: every proxy in both
of this project's deployment shapes *appends*, so anything the client prepends
sits to the left of the real address and is never reached. A client who sends
``X-Forwarded-For: 9.9.9.9`` is still bucketed under their own address.

Trusted-proxy set: RFC-1918 private ranges + loopback. Correct for both shapes:
  * Home server — the app sits behind nginx on the same host, so ``REMOTE_ADDR``
    is loopback (trusted) and nginx appends the real client to
    ``X-Forwarded-For`` (rightmost) via ``proxy_params``.
  * Render / Railway — the container's ``REMOTE_ADDR`` is a private platform
    address (trusted), so the XFF path is taken.
"""

import ipaddress
import json
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


# ── Central rate-limit configuration ─────────────────────────────────────────
# Every throttled endpoint reads its rate from here so all limits live in one
# place. The values come from settings.RATE_LIMITS, sourced from RATE_LIMIT_*
# env vars (config/settings.py). Format is "count/period" (period: s, m, h, d,
# or a multiple like "10m"). The *key* a limit counts against (IP, IP+email,
# email) and its *group* are set on the decorator at the URL, not here.
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

# Loopback is called out separately from the rest of the trusted set. Both are
# "a proxy hop", but only loopback is *also* a legitimate terminus: under
# `runserver` and the test client there is no proxy and no XFF, and that is
# normal rather than a misconfiguration. Falling back to a private non-loopback
# address means a PaaS deployment stopped sending XFF, which is worth shouting
# about — see the warning in resolve_client_ip.
_LOOPBACK_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
]


def _parse_ip(value: str):
    """Return an ip_address for `value`, or None if it isn't one.

    Every candidate address is funnelled through here, which is what keeps
    non-IP text out of rate-limit keys and out of the audit trail. Before this
    existed, an XFF entry that was not an IP at all was returned verbatim, and
    from the audit trail it reached a varchar(45) column — where anything over
    45 characters raised a DataError and turned a public endpoint into a 500
    (and, worse, into a user-enumeration oracle). See D7/D8/D10 in
    docs/RATE_LIMITING_AUDIT.md.
    """
    try:
        return ipaddress.ip_address(value.strip())
    except (ValueError, AttributeError):
        return None


def _in_networks(addr, networks) -> bool:
    return any(addr in net for net in networks)


def _is_trusted_proxy(ip_str: str) -> bool:
    """Return True if ip_str parses as an address inside a trusted proxy range."""
    addr = _parse_ip(ip_str)
    return addr is not None and _in_networks(addr, _TRUSTED_NETWORKS)


# ── The resolver ──────────────────────────────────────────────────────────────

def resolve_client_ip(request) -> str:
    """
    The exact, validated client IP. See the module docstring for the algorithm.

    Signature is ``(request)`` — one argument — because three of the four
    consumers want it that way (DRF's ``get_ident``, django-ratelimit's
    ``RATELIMIT_IP_META_KEY``, and a plain call from a view). The
    django-ratelimit *key callable* signature ``(group, request)`` is provided
    by the thin adapters further down, so no consumer is ever tempted to write a
    fifth implementation to fit its own signature.

    Raises ImproperlyConfigured when ``REMOTE_ADDR`` is missing or unparseable,
    which mirrors what django-ratelimit's own ``_get_ip`` does. That can only
    happen on a deployment that is already broken (typically a Unix-socket
    upstream that never sets it), and failing loudly beats silently bucketing
    every client in the world together.
    """
    remote = request.META.get("REMOTE_ADDR", "") or ""

    if not _is_trusted_proxy(remote):
        # Direct connection from an untrusted address — proxy was bypassed.
        # Ignore X-Forwarded-For; it is fully attacker-controlled here.
        if _parse_ip(remote) is None:
            raise ImproperlyConfigured(
                "REMOTE_ADDR is empty or not an IP address (%r). The app cannot "
                "identify clients, so rate limiting would be meaningless. This "
                "usually means the upstream connects over a Unix socket without "
                "setting it — configure the proxy to pass the real address."
                % remote
            )
        return remote

    # Behind a trusted proxy: find the rightmost untrusted, well-formed entry.
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "") or ""
    if xff:
        for candidate in reversed(xff.split(",")):
            addr = _parse_ip(candidate)
            if addr is not None and not _in_networks(addr, _TRUSTED_NETWORKS):
                return str(addr)

    # XFF absent, or every entry was a trusted proxy or garbage. REMOTE_ADDR is
    # the best available answer, but it is a *proxy* address — so every client
    # behind that proxy now shares one bucket. That is fine for loopback (local
    # dev and the test client, where there genuinely is no proxy) and is a real
    # misconfiguration anywhere else, so make the second case visible instead of
    # letting the whole site quietly share one rate-limit bucket.
    if not _in_networks(_parse_ip(remote), _LOOPBACK_NETWORKS):
        logger.warning(
            "Could not resolve a client IP from X-Forwarded-For; falling back to "
            "the proxy address, so all clients behind it share one rate-limit "
            "bucket. Configure the edge to send X-Forwarded-For.",
            extra={
                "event": "client_ip_unresolved",
                "remote_addr": remote,
                "has_xff": bool(xff),
            },
        )
    return remote


def bucket_ip(request) -> str:
    """
    ``resolve_client_ip`` reduced to the prefix a rate-limit bucket should use.

    IPv4 is masked to /32 and IPv6 to /64 by default, reading the same
    ``RATELIMIT_IPV4_MASK`` / ``RATELIMIT_IPV6_MASK`` settings django-ratelimit
    uses for its own built-in ``key='ip'``. One set of knobs, honoured on every
    path, whichever one a future endpoint happens to take.

    The IPv6 default is the point of this function. A residential or mobile IPv6
    allocation is a /64 or larger and the client owns every address inside it, so
    an unmasked key hands them a brand-new bucket for every request and every
    IP-keyed limit in the project becomes free to bypass. Masking to the prefix
    makes the bucket the thing the client can't change.

    Read via getattr at call time, not at import, so @override_settings works.
    """
    ip = resolve_client_ip(request)
    addr = _parse_ip(ip)
    if addr is None:  # pragma: no cover — resolve_client_ip already guarantees this
        return ip

    if addr.version == 6:
        mask = getattr(settings, "RATELIMIT_IPV6_MASK", 64)
    else:
        mask = getattr(settings, "RATELIMIT_IPV4_MASK", 32)

    network = ipaddress.ip_network(f"{addr}/{mask}", strict=False)
    return str(network.network_address)


# ── Body parsing ──────────────────────────────────────────────────────────────

def _submitted_email(request) -> str:
    """
    The email from a JSON request body, normalised, or "" if there isn't one.

    As django-ratelimit key callables run with the limiter placed outermost
    (wrapping ``.as_view()`` at the URL), they receive the raw Django request —
    DRF's ``request.data`` is not yet available. We parse ``request.body``
    directly; DRF re-parses the buffered body downstream, so reading it here is
    safe.

    JSON only, which is why every endpoint keyed on the email is JSON-only: a
    ``multipart/form-data`` post yields "" and degrades to IP-only bucketing.
    """
    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, AttributeError):
        return ""
    if not isinstance(body, dict):
        return ""
    return (body.get("email") or "").lower().strip()


# ── django-ratelimit key callables ───────────────────────────────────────────
# Thin adapters over the two resolvers above, matching django-ratelimit's
# (group, request) callable signature. Every rate-limited URL passes one of
# these, plus an explicit `group=` — see the _rl helpers in the two urls.py.

def client_ip(group, request) -> str:
    """Bucket per client IP prefix. The default for IP-shaped limits."""
    return bucket_ip(request)


def ip_and_email(group, request) -> str:
    """
    Composite key: rate-limits per (IP, email) pair, so a single IP cannot
    enumerate accounts by hammering many emails and a single email cannot be
    flooded from one IP. Falls back to IP-only when the body has no email.

    Note what this shape does NOT protect: where the email is *attacker-chosen*
    and free — public lead capture — varying it buys a fresh bucket every time.
    Endpoints like that need an IP-only tier beside this one, which is why
    ``POST /inquiries/`` carries both.
    """
    return f"{bucket_ip(request)}:{_submitted_email(request)}"


def email_key_for(email: str):
    """A key callable bucketing per submitted email, built from an ALREADY-PARSED
    address.

    This is the tier that caps attempts against **one account** no matter how
    many source addresses they come from — the axis an IP-keyed login limit
    leaves wide open to a distributed credential-stuffing run.

    It takes the email as an argument rather than reading it off the request,
    because its one caller runs INSIDE the view: parsing ``request.body`` there
    raises ``RawPostDataException``, since DRF has already consumed the stream
    (the failure mode apps/inquiries/dedupe.py documents at length). The
    URL-level variant that read the body itself was removed when login moved into
    apps/accounts/login_guard.py and left it with no callers.

    Falls back to the IP prefix (namespaced, so it can never collide with a
    literal email) when no email was submitted, so a stream of bodiless POSTs
    doesn't all land in one shared bucket.
    """
    normalised = email_bucket_value(email)

    def key(group, request) -> str:
        return normalised if normalised else f"ip:{bucket_ip(request)}"

    return key


def email_bucket_value(email: str) -> str:
    """The exact string an email-keyed limit counts against.

    Split out so the admin can rebuild a bucket's cache key to CLEAR it without
    re-deriving the normalisation and risking a mismatch — a "release this
    account" button that deletes the wrong key would silently do nothing.
    """
    return (email or "").lower().strip()


