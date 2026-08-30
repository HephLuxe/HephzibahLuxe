"""
apps/core/views.py

Health-check endpoints (part of the observability standard — see
docs/OBSERVABILITY_STANDARD.md). Plain Django views, deliberately NOT DRF
``@api_view`` functions, so they carry no authentication or throttling and stay
dependency-free — the home-server control panel and uptime monitors need a bare
unauthenticated 200.
"""

import logging

from django.conf import settings
from django.http import JsonResponse

from apps.core.error_codes import RATE_LIMITED

logger = logging.getLogger(__name__)


def health_live(request):
    """
    Liveness probe — no I/O. Confirms the process is running.
    Uptime monitors / the home-server control-panel wizard: GET /health/
    """
    return JsonResponse({"status": "ok"})


def health_ready(request):
    """
    Readiness probe — verifies DB (and cache, if configured) are reachable
    before returning 200. GET /health/ready/
    Returns 503 when a dependency is unreachable so a proxy withholds traffic.
    """
    errors = {}

    try:
        from django.db import connection
        connection.ensure_connection()
    except Exception:
        # The exception STRING is not safe to return. psycopg renders it as
        # `connection to server at "ep-xxx.neon.tech" (1.2.3.4), port 5432
        # failed: ... for user "role"` — host, resolved IP, port and the database
        # role, from an endpoint that is deliberately unauthenticated and
        # unthrottled. It only ever appeared during an outage, which is precisely
        # when nobody was reading the response body.
        #
        # The KEY stays. RUNBOOK's triage step reads `errors.db` vs
        # `errors.cache` to tell a bad DATABASE_URL from a bad CACHE_REDIS_URL,
        # and that distinction is the whole diagnostic value of this endpoint —
        # it survives, only the detail moves to the log.
        logger.exception(
            "Readiness probe: database unreachable.",
            extra={"event": "health_dependency_down", "dependency": "db"},
        )
        errors["db"] = "unreachable"

    # Cache is optional (Redis when CACHE_REDIS_URL is set, else a LocMem
    # fallback that always answers) — so this only meaningfully checks a real
    # Redis cache, and never fails readiness just because caching isn't wired up.
    try:
        from django.core.cache import cache
        cache.get("__healthcheck__")
    except Exception:
        # Same reasoning as the db branch. Leaks less — redis-py renders host and
        # port but not the password — but `redis.railway.internal:6379` is still
        # internal topology being handed to an anonymous caller.
        logger.exception(
            "Readiness probe: cache unreachable.",
            extra={"event": "health_dependency_down", "dependency": "cache"},
        )
        errors["cache"] = "unreachable"

    if errors:
        return JsonResponse({"status": "error", "errors": errors}, status=503)
    return JsonResponse({"status": "ok"})


def ratelimited(request, exception):
    """
    Rendered by django_ratelimit.middleware.RatelimitMiddleware (via
    settings.RATELIMIT_VIEW) when a per-endpoint rate limit blocks a request.
    Returns the project-standard error envelope so the frontend branches on the
    machine-readable ``code`` rather than the ``detail`` string.

    ``code`` is ``rate_limited``, distinct from the ``throttled_global`` that
    DRF's shared anon ceiling produces — see apps/core/error_codes.py.

    Emits ONE structured log line per block. Nothing used to be logged here at
    all, which meant the obvious question — "is anyone actually hitting these
    limits, and which one?" — had no answer, and every proposal to retune a rate
    was a guess. The path is what identifies the limit that fired (there is one
    limited endpoint per path), and the resolved client IP is what distinguishes
    one attacker from a NAT full of real users. Alerting is not done here: per
    docs/OBSERVABILITY_STANDARD.md the app emits ``event=`` and the monitoring
    stack decides who gets paged.
    """
    # Imported here rather than at module scope: this module is imported by the
    # health checks, which must stay dependency-free, and apps.core.ratelimit
    # reads settings.RATE_LIMITS at import time.
    from apps.core.ratelimit import resolve_client_ip

    retry_after = _retry_after(request, exception)
    logger.warning(
        "Rate limit exceeded.",
        extra={
            "event": "rate_limited",
            "path": request.path,
            "method": request.method,
            "client_ip": resolve_client_ip(request),
            # Logged because a long wait and a short one are different incidents:
            # 60s is someone clicking too fast, 80,000s is a daily cap and
            # probably a script.
            "retry_after": retry_after,
        },
    )

    response = JsonResponse(
        {
            "detail": "Rate limit exceeded. Please try again later.",
            "code": RATE_LIMITED,
        },
        status=429,
    )
    response["Retry-After"] = str(retry_after)
    return response


def _retry_after(request, exception) -> int:
    """Seconds until the caller can genuinely try again.

    This used to be a flat 60 for every django-ratelimit 429, which is a lie on a
    daily cap: a client that respects the header retries every minute for up to
    24 hours, is refused each time, and looks broken while filling the logs with
    a 1,440-a-day heartbeat. The number was flat because the middleware is handed
    only the exception, and django-ratelimit raises a bare ``Ratelimited()`` that
    says nothing about which tier fired or when its window closes.

    The information does exist — ``get_usage`` returns ``time_left`` — it simply
    was not reaching here. Two ways it now does, in order of preference:

    1. ``exception.retry_after``, set by a caller that already knows. Login
       raises its own ``Ratelimited`` after checking the tiers itself, so it has
       the answer in hand (apps/accounts/views.MyTokenObtainPairView).
    2. ``request.rate_limit_tiers``, stashed by the ``_rl`` helpers in
       apps/accounts/urls.py and apps/inquiries/urls.py before the decorators
       run. Re-checking those tiers with ``increment=False`` finds the full one.

    **The largest wait wins**, not the first. An endpoint can be over more than
    one ceiling at once, and reporting the burst's 60 seconds while the day is
    also full guarantees the next attempt is refused too.

    Falls back to ``RATELIMIT_RETRY_AFTER_SECONDS`` when neither path yields
    anything — an endpoint limited some other way, or rate limiting disabled
    under the test runner. The setting stays as the last resort rather than the
    only answer.
    """
    from django_ratelimit.core import get_usage

    explicit = getattr(exception, "retry_after", None)
    if explicit is not None:
        return max(1, int(explicit))

    waits = []
    for group, rate, key in getattr(request, "rate_limit_tiers", ()) or ():
        usage = get_usage(
            request, group=group, key=key, rate=rate, method="POST", increment=False,
        )
        # count > limit here, not >=: the decorator already counted the request
        # being refused, so a tier sitting exactly AT its limit still had room
        # for it and is not the one that fired.
        if usage is not None and usage["count"] > usage["limit"]:
            waits.append(usage["time_left"])

    if waits:
        return max(1, int(max(waits)))
    return getattr(settings, "RATELIMIT_RETRY_AFTER_SECONDS", 60)
