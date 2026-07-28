"""
apps/core/logging.py

Structured JSON logging (part of the observability standard — see
docs/OBSERVABILITY_STANDARD.md).

Three pieces:

* ``RequestContextFilter`` — injects ``request_id`` and ``user_id`` (read from
  the contextvars set by RequestIDMiddleware) onto every log record, so each
  line can be traced back to a single request/operation.
* ``HephJsonFormatter`` — renders records as one JSON object per line with a
  stable field set (timestamp, level, logger, message, request_id, user_id …).
* ``scrub`` — recursively redacts sensitive values. Used by the formatter (so
  no secret slips into a log line via ``extra=``) and re-used by the Sentry
  ``before_send`` hook so both paths share one redaction policy.

In production stdout is JSON. Locally the console handler stays human-readable;
this module owns the JSON path. Unlike the dev-only Loki wiring elsewhere, the
Loki handler here is a first-class, env-gated sink usable in every environment
(``LOKI_PUSH_URL`` set) so home + cloud deploys emit identically to the owner's
Loki/Grafana stack.
"""

import logging

try:  # python-json-logger >= 3.1 moved the formatter to .json
    from pythonjsonlogger.json import JsonFormatter
except ImportError:  # pragma: no cover - older releases
    from pythonjsonlogger.jsonlogger import JsonFormatter

from apps.core.middleware import request_id_var, user_id_var

# Keys whose values must never appear in a log line or a Sentry payload. Matched
# case-insensitively, by substring, against dict keys.
SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "api-key",
    "temporary_password",
    "code",
    "brevo_api_key",
)

REDACTED = "[redacted]"
_MAX_DEPTH = 6


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    return any(part in k for part in SENSITIVE_KEY_PARTS)


def scrub(value, _depth: int = 0):
    """Return a copy of ``value`` with sensitive dict entries redacted."""
    if _depth > _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {
            k: (REDACTED if _is_sensitive(str(k)) else scrub(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(v, _depth + 1) for v in value]
    return value


class RequestContextFilter(logging.Filter):
    """Attach request_id / user_id from the active request context."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        return True


class HephJsonFormatter(JsonFormatter):
    """JSON formatter with a stable field set and value scrubbing."""

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record.setdefault("request_id", getattr(record, "request_id", None))
        log_record.setdefault("user_id", getattr(record, "user_id", None))
        # Redact any sensitive values that arrived via logger(..., extra={...}).
        for key, value in list(log_record.items()):
            if _is_sensitive(key):
                log_record[key] = REDACTED
            elif isinstance(value, (dict, list, tuple)):
                log_record[key] = scrub(value)


def build_logging_config(
    log_level: str = "INFO",
    json_logs: bool = True,
    loki_url: str = "",
    loki_auth: "tuple[str, str] | None" = None,
    loki_tags: "dict | None" = None,
) -> dict:
    """Return a dictConfig dict. JSON in prod, console-friendly in dev.

    ``loki_url`` is optional and env-gated: leave it unset (the default) and no
    Loki handler is added, so ``logging_loki`` is never referenced. When set,
    the same ``HephJsonFormatter`` used for stdout is reused for the Loki
    handler, so PII scrubbing (``scrub()``) applies to whatever ships off-box.
    """
    handler = "json" if json_logs else "console"
    active_handlers = [handler] + (["loki"] if loki_url else [])

    handlers = {
        "json": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_context"],
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
            "filters": ["request_context"],
        },
    }
    if loki_url:
        import queue as _queue

        handlers["loki"] = {
            "class": "logging_loki.LokiQueueHandler",
            "queue": _queue.Queue(-1),
            "url": loki_url,
            "auth": loki_auth,
            "tags": loki_tags or {},
            "version": "1",
            "formatter": "json",
            "filters": ["request_context"],
        }

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_context": {
                "()": "apps.core.logging.RequestContextFilter",
            },
        },
        "formatters": {
            "json": {
                "()": "apps.core.logging.HephJsonFormatter",
                "format": "%(timestamp)s %(level)s %(message)s",
                "rename_fields": {"asctime": "timestamp"},
                "timestamp": True,
            },
            "console": {
                "format": "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s",
            },
        },
        "handlers": handlers,
        "root": {
            "handlers": active_handlers,
            "level": log_level,
        },
        "loggers": {
            # Django request errors (unhandled 500s) — let our handler/Sentry
            # own them; keep Django's own logger from double-formatting.
            "django.request": {
                "handlers": active_handlers,
                "level": "ERROR",
                "propagate": False,
            },
            "apps": {
                "handlers": active_handlers,
                "level": log_level,
                "propagate": False,
            },
        },
    }
