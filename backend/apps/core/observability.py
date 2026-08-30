"""
apps/core/observability.py

Observability wiring: Sentry (→ GlitchTip) initialisation (part of the
observability standard — see docs/OBSERVABILITY_STANDARD.md).

``init_sentry`` is called from settings, guarded by SENTRY_DSN so it is a no-op
in local/dev/test (no DSN configured). ``before_send`` reuses the same scrub
policy as structured logging (apps/core/logging.scrub) so no secret leaks into
an event payload.

Correlation-id propagation into background work is no longer wired here. It used
to need three Celery signal handlers to carry ``request_id_var`` across the
broker as a task header. Deferred work now runs in a thread inside the same
process, so apps/core/background copies the caller's ``contextvars`` into the
worker thread directly — the id travels by construction rather than by protocol.
"""

import logging

from apps.core.logging import scrub

logger = logging.getLogger("apps.core.observability")


def init_sentry(dsn: str, environment: str, release: "str | None" = None,
                traces_sample_rate: float = 0.1) -> None:
    """Initialise Sentry (GlitchTip-compatible).

    No-op if the SDK isn't installed. Points at GlitchTip via the DSN — GlitchTip
    speaks the Sentry ingest protocol, so no GlitchTip-specific client is needed.
    """
    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.redis import RedisIntegration
    except ImportError:  # pragma: no cover
        logger.warning("SENTRY_DSN set but sentry-sdk is not installed; skipping.")
        return

    def before_send(event, hint):
        if "request" in event and isinstance(event["request"], dict):
            for field in ("data", "cookies", "headers"):
                if field in event["request"]:
                    event["request"][field] = scrub(event["request"][field])
        if "extra" in event:
            event["extra"] = scrub(event["extra"])
        return event

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        integrations=[
            DjangoIntegration(),
            RedisIntegration(),
        ],
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,
        before_send=before_send,
    )
