"""
apps/core/observability.py

Observability wiring: Sentry (→ GlitchTip) initialisation + Celery request-id
propagation (part of the observability standard — see
docs/OBSERVABILITY_STANDARD.md).

``init_sentry`` is called from settings, guarded by SENTRY_DSN so it is a no-op
in local/dev/test (no DSN configured). ``before_send`` reuses the same scrub
policy as structured logging (apps/core/logging.scrub) so no secret leaks into
an event payload.

The Celery signal handlers carry the web request's correlation id into the
worker: the id is attached as a task header when a task is published, and
restored into the worker's contextvar when the task runs — so a job's logs and
errors share the id of the request that enqueued it.
"""

import logging

from celery.signals import before_task_publish, task_postrun, task_prerun

from apps.core.logging import scrub
from apps.core.middleware import request_id_var

logger = logging.getLogger("apps.core.observability")

_REQUEST_ID_HEADER = "x_request_id"


def init_sentry(dsn: str, environment: str, release: "str | None" = None,
                traces_sample_rate: float = 0.1) -> None:
    """Initialise Sentry (GlitchTip-compatible) for web + Celery.

    No-op if the SDK isn't installed. Points at GlitchTip via the DSN — GlitchTip
    speaks the Sentry ingest protocol, so no GlitchTip-specific client is needed.
    """
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
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
            CeleryIntegration(),
            RedisIntegration(),
        ],
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,
        before_send=before_send,
    )


# ---------------------------------------------------------------------------
# Celery correlation-id propagation
# ---------------------------------------------------------------------------

@before_task_publish.connect
def _attach_request_id(headers=None, **kwargs):
    """Stamp the current request id onto the outgoing task's headers."""
    if headers is not None:
        rid = request_id_var.get()
        if rid:
            headers[_REQUEST_ID_HEADER] = rid


@task_prerun.connect
def _restore_request_id(task=None, **kwargs):
    """Restore the correlation id into the worker's context for this task run."""
    rid = None
    request = getattr(task, "request", None)
    if request is not None:
        rid = getattr(request, _REQUEST_ID_HEADER, None)
    request_id_var.set(rid)


@task_postrun.connect
def _clear_request_id(**kwargs):
    request_id_var.set(None)
