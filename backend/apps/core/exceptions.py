"""
apps/core/exceptions.py

Project-wide DRF exception handler with two responsibilities:

1. Inject `code` into responses DRF already built for handled exceptions
   (auth, permission, not-found, throttle, validation). This is what makes
   `enforce()` (apps.core.permissions) and `get_object_or_404` calls, which
   are scattered across every app, produce the standard envelope
   ``{detail, code, errors?}`` without touching each call site individually.
   It also normalises the one shape DRF gets wrong for our purposes: a bare
   ``raise ValidationError("message")`` from a services.py function produces
   a raw list body (`["message"]`), not a dict — we rewrap that into the
   envelope too.

2. Handle the case those miss: an *unexpected* exception bubbling out of a
   view — a real bug. For those we log the full traceback as one structured
   record, report to Sentry if configured, and return the standard 500
   envelope with ``code=internal_error``.

Views that build their own error responses directly (not via a raised
exception) still need their own local ``_error()`` helper — this handler only
ever sees exceptions that actually propagate out of a view.
"""

import logging

from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework_simplejwt.exceptions import InvalidToken

from apps.core.error_codes import (
    AUTHENTICATION_REQUIRED,
    INTERNAL_ERROR,
    INVALID_CREDENTIALS,
    NOT_FOUND,
    PERMISSION_DENIED,
    RATE_LIMITED,
    TOKEN_INVALID,
    VALIDATION_ERROR,
)

logger = logging.getLogger("apps.core.exceptions")

try:  # Sentry is optional (off in dev/test where no DSN is configured).
    import sentry_sdk
except ImportError:  # pragma: no cover
    sentry_sdk = None


# Order matters: the first matching entry wins, so more specific exception
# types must precede their parents. InvalidToken (SimpleJWT) is itself an
# AuthenticationFailed subclass but means something different to a frontend
# (stale/garbage refresh token vs. a rejected login) — check it first.
_CODE_BY_EXCEPTION = (
    (InvalidToken, TOKEN_INVALID),
    (NotAuthenticated, AUTHENTICATION_REQUIRED),
    (AuthenticationFailed, INVALID_CREDENTIALS),
    (PermissionDenied, PERMISSION_DENIED),
    (NotFound, NOT_FOUND),
    (Throttled, RATE_LIMITED),
    (ValidationError, VALIDATION_ERROR),
)


def _apply_envelope(exc: Exception, response: Response) -> None:
    """Mutate response.data in place so it carries the standard envelope."""
    code = None
    for exc_type, mapped_code in _CODE_BY_EXCEPTION:
        if isinstance(exc, exc_type):
            code = mapped_code
            break
    if code is None:
        return  # an exception type we don't map (e.g. MethodNotAllowed) — leave DRF's shape alone

    if isinstance(response.data, dict):
        response.data.setdefault("code", code)
        return

    # DRF's ValidationError forces any non-dict/list detail into a list, so
    # `raise ValidationError("message")` (the pattern every services.py in
    # this project uses) produces a bare list body, not `{"detail": ...}`.
    # Normalise it into the envelope so the frontend always sees the same shape.
    detail = response.data[0] if response.data else "Validation failed."
    response.data = {
        "detail": str(detail),
        "code": code,
        "errors": {"non_field_errors": response.data},
    }


def custom_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)

    if response is not None:
        # DRF already mapped this to a 4xx (or throttle 429) — add `code`.
        _apply_envelope(exc, response)
        return response

    # Unhandled — a genuine server error.
    request = context.get("request")
    view = context.get("view")
    logger.exception(
        "Unhandled exception in %s",
        getattr(view, "__class__", type(view)).__name__ if view else "view",
        extra={
            "path": getattr(request, "path", None),
            "method": getattr(request, "method", None),
            "exception_type": type(exc).__name__,
        },
    )

    if sentry_sdk is not None:
        sentry_sdk.capture_exception(exc)

    return Response(
        {
            "detail": "An unexpected error occurred. Please try again.",
            "code": INTERNAL_ERROR,
        },
        status=500,
    )
