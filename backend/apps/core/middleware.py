"""
apps/core/middleware.py

Request-correlation middleware (part of the observability standard — see
docs/OBSERVABILITY_STANDARD.md).

Every inbound request is tagged with a request ID (taken from an inbound
``X-Request-ID`` header if present, else freshly generated). The ID is stashed
in a ``ContextVar`` so the logging filter (apps/core/logging.RequestContextFilter)
can read it without threading it through every call.

The same ID is echoed back on the response and carried into background work: a
``ContextVar`` is not inherited by a new thread, so apps/core/background copies
the caller's context into the worker thread explicitly (this replaced three
Celery signal handlers that used to carry the id across the broker as a task
header). One logical operation — a web request plus any background job it
dispatches — therefore shares a single correlation ID end to end.

``user_id`` is captured best-effort once the view has run (DRF resolves
``request.user`` lazily during the view, so it is not reliably known earlier).
Logs emitted *before* that point still carry the request ID, which is the
primary correlation key.

NOTE: this is a separate concern from config/middleware.py's
ForcePasswordChangeMiddleware — both are registered and coexist.
"""

import contextvars
import uuid

# Default to None outside a request (management commands, the run_scheduled cron
# groups) so log records still format cleanly with no request in scope.
request_id_var: "contextvars.ContextVar[str | None]" = contextvars.ContextVar(
    "request_id", default=None
)
user_id_var: "contextvars.ContextVar[str | None]" = contextvars.ContextVar(
    "user_id", default=None
)

REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id() -> "str | None":
    return request_id_var.get()


def set_user_id(user_id: "str | None") -> None:
    """Allow auth/views to publish the active user id for log correlation."""
    user_id_var.set(str(user_id) if user_id is not None else None)


class RequestIDMiddleware:
    """Assign/propagate a correlation ID for the lifetime of each request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = request.headers.get(REQUEST_ID_HEADER, "").strip() or uuid.uuid4().hex
        request.request_id = rid
        rid_token = request_id_var.set(rid)
        user_token = user_id_var.set(None)
        try:
            response = self.get_response(request)
            user = getattr(request, "user", None)
            if user is not None and getattr(user, "is_authenticated", False):
                user_id_var.set(str(user.pk))
            response[REQUEST_ID_HEADER] = rid
            return response
        finally:
            # Reset so a reused worker thread/greenlet never leaks IDs.
            request_id_var.reset(rid_token)
            user_id_var.reset(user_token)
