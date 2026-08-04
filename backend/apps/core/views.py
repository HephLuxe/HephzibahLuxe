"""
apps/core/views.py

Health-check endpoints (part of the observability standard — see
docs/OBSERVABILITY_STANDARD.md). Plain Django views, deliberately NOT DRF
``@api_view`` functions, so they carry no authentication or throttling and stay
dependency-free — the home-server control panel and uptime monitors need a bare
unauthenticated 200.
"""

from django.http import JsonResponse

from apps.core.error_codes import RATE_LIMITED


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
    except Exception as exc:
        errors["db"] = str(exc)

    # Cache is optional (Redis when CACHE_REDIS_URL is set, else a LocMem
    # fallback that always answers) — so this only meaningfully checks a real
    # Redis cache, and never fails readiness just because caching isn't wired up.
    try:
        from django.core.cache import cache
        cache.get("__healthcheck__")
    except Exception as exc:
        errors["cache"] = str(exc)

    if errors:
        return JsonResponse({"status": "error", "errors": errors}, status=503)
    return JsonResponse({"status": "ok"})


def ratelimited(request, exception):
    """
    Rendered by django_ratelimit.middleware.RatelimitMiddleware (via
    settings.RATELIMIT_VIEW) when a rate-limited endpoint blocks a request.
    Returns the project-standard error envelope so the frontend branches on the
    machine-readable ``code`` rather than the ``detail`` string.
    """
    return JsonResponse(
        {
            "detail": "Rate limit exceeded. Please try again later.",
            "code": RATE_LIMITED,
        },
        status=429,
    )
