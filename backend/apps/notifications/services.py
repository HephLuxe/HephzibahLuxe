"""
apps/notifications/services.py

All outbound email goes through queue_notification() -> a durable Notification
row -> tasks.send_notification_task -> send_now() -> Brevo's transactional email
API. See apps/notifications/README.md for the full template list and each one's
expected params, and docs/adr/0001-remove-celery.md for how the send is deferred
now that there is no broker.
"""

from __future__ import annotations

import contextlib
import datetime
import logging
import threading
import uuid
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from .models import (
    Notification,
    NotificationStatus,
    NotificationType,
    NotificationTypeSettings,
    ServiceHealthState,
)

logger = logging.getLogger(__name__)

# The ServiceHealthState.service key + probe target for Brevo.
BREVO_SERVICE = "brevo"
# Seconds before a hung Brevo call is abandoned — without this a single stalled
# request can pin a background pool thread indefinitely, and with
# BACKGROUND_MAX_WORKERS=4 four of them would take every send down with it.
BREVO_REQUEST_TIMEOUT = 10

# Templates whose `context` contains a live credential: the 6-digit reset code
# and the generated temporary password (see apps/accounts/utils.py). Notification
# .context is a JSONField holding the exact params handed to Brevo, so without
# the scrub below a successful credentials email left a plaintext password in
# Postgres for the full 90-day retention window — and a failed one left it there
# forever, since the weekly cleanup only ever deleted SENT rows. Anyone with DB
# access, a Postgres backup, or the Django admin could read them in the clear.
AUTH_SECRET_TEMPLATES = ("password_reset", "user_credentials")
# What replaces it. A marker rather than `{}` so a support engineer looking at an
# empty context can tell "scrubbed on purpose" from "never had one".
REDACTED_CONTEXT = {"redacted": True}

try:  # Sentry is optional (off in dev/test where no DSN is configured).
    import sentry_sdk
except ImportError:  # pragma: no cover
    sentry_sdk = None


def _emit_brevo_outage(extra: dict) -> None:
    """One high-severity signal per outage (called only on an up -> down
    transition), so Grafana/GlitchTip fire a single ntfy alert, not one per
    failed send. Matched by the `event=brevo_outage` label in alert rules."""
    logger.error(
        "Brevo appears to be DOWN — transactional email is failing.",
        extra={"event": "brevo_outage", "service": BREVO_SERVICE, **extra},
    )
    if sentry_sdk is not None:
        sentry_sdk.capture_message("Brevo outage detected — transactional email failing", level="error")


def _emit_brevo_recovered() -> None:
    logger.info(
        "Brevo has recovered — resuming transactional email.",
        extra={"event": "brevo_recovered", "service": BREVO_SERVICE},
    )


# Set while retry_failed_notifications_task is running on this thread. The sweep
# forces sends even while the breaker is open, so the first one to succeed flips
# Brevo up -> and that transition calls _drain_after_recovery, which would
# re-enter the very sweep that is already draining. Harmless (every re-driven row
# hits the SENT idempotent guard) but pointless work, and the recursion is not
# obvious from either call site.
_sweeping = threading.local()


@contextlib.contextmanager
def sweep_in_progress():
    """Mark this thread as already draining the backlog."""
    _sweeping.active = True
    try:
        yield
    finally:
        _sweeping.active = False


def _drain_after_recovery() -> None:
    """On recovery, immediately re-drive the backlog rather than waiting for the
    next cron pass. Reuses the same re-queue logic (the retry_failed sweep).

    A no-op when the sweep is what detected the recovery — this cycle is already
    the drain.
    """
    if getattr(_sweeping, "active", False):
        return
    from .tasks import retry_failed_notifications_task
    retry_failed_notifications_task.delay()


def scrub_auth_context(queryset) -> int:
    """
    Redact `context` on any row in `queryset` whose template carries a
    credential. Returns how many rows were scrubbed.

    Bulk form, for the paths that mark rows terminal with a single UPDATE (the
    give-up pass in the retry sweep) and for the one-off backfill migration.
    """
    return queryset.filter(template_name__in=AUTH_SECRET_TEMPLATES).exclude(
        context=REDACTED_CONTEXT
    ).update(context=REDACTED_CONTEXT)


# Maps a NotificationType value to the config/settings.py attribute holding
# its numeric Brevo template ID. Every NotificationType must have an entry
# here — a template_name with no configured ID is a configuration error
# (fails loudly in send_now), not a graceful-degradation case, since every
# type has a real Brevo template.
TEMPLATE_ID_MAP = {
    NotificationType.NEW_REMINDER: "BREVO_TEMPLATE_NEW_REMINDER",
    NotificationType.PAYMENT_DUE: "BREVO_TEMPLATE_PAYMENT_DUE",
    NotificationType.MEETING_PREP_DUE: "BREVO_TEMPLATE_MEETING_PREP_DUE",
    NotificationType.PHASE_ADVANCED: "BREVO_TEMPLATE_PHASE_ADVANCED",
    NotificationType.EVENT_DETAILS_UPDATED: "BREVO_TEMPLATE_EVENT_DETAILS_UPDATED",
    NotificationType.DOCUMENT_ADDED: "BREVO_TEMPLATE_DOCUMENT_ADDED",
    NotificationType.INVOICE_ISSUED: "BREVO_TEMPLATE_INVOICE_ISSUED",
    NotificationType.RECEIPT_ISSUED: "BREVO_TEMPLATE_RECEIPT_ISSUED",
    NotificationType.MILESTONE_PAID: "BREVO_TEMPLATE_MILESTONE_PAID",
    NotificationType.USER_CREDENTIALS: "BREVO_TEMPLATE_USER_CREDENTIALS",
    NotificationType.PASSWORD_RESET: "BREVO_TEMPLATE_PASSWORD_RESET",
    NotificationType.INQUIRY_RECEIVED: "BREVO_TEMPLATE_INQUIRY_RECEIVED",
    NotificationType.INQUIRY_SUBMITTED_INTERNAL: "BREVO_TEMPLATE_INQUIRY_SUBMITTED_INTERNAL",
}


def queue_notification(
    *,
    recipient_email: str,
    template_name: str,
    context: dict,
    recipient_user=None,
    engagement=None,
) -> Notification | None:
    """
    Create a durable Notification row and dispatch the send. This is the one
    entry point every other app should call to email a client — never call
    Brevo's API directly from a view or another app's service.

    Dispatch order matters and is handled inside `.delay()`: in the web process
    it defers to `transaction.on_commit`, so the send can never observe a row its
    own transaction has not committed. Several callers run inside
    `transaction.atomic()` (portal, document_hub and accounts services,
    document_hub and meetings views) and under Celery this was a latent race that
    the broker's network hop usually lost for us — a rollback after
    queue_notification could still email a client about a record that no longer
    exists. A thread would not have lost that race.

    Honors the per-type switch (NotificationTypeSettings, admin-managed): when
    this specific template_name is disabled, this is a no-op returning None —
    other notification types are unaffected. Callers ignore the return value.
    """
    if not NotificationTypeSettings.is_enabled(template_name):
        return None

    if template_name not in NotificationType.values:
        raise ValueError(f"Unknown notification template_name: '{template_name}'")

    notification = Notification.objects.create(
        recipient_email=recipient_email,
        recipient_user=recipient_user,
        engagement=engagement,
        template_name=template_name,
        # The real subject line lives in the Brevo template now — this is
        # only ever shown in the admin audit-trail column.
        subject=NotificationType(template_name).label,
        context=_serialise_context(context),
    )

    from .tasks import send_notification_task
    send_notification_task.delay(str(notification.id))
    return notification


def send_now(notification: Notification) -> bool:
    """
    Actually send the email via Brevo's transactional API. Returns True on
    success, False on failure. Never raises: any exception from the API call is
    caught and recorded on the notification itself.

    Increments `attempt_count` on every real attempt, which is why there is no
    in-task retry loop anywhere — three tries inside one dispatch would spend
    the whole budget and the retry sweep (`attempt_count__lt=MAX_ATTEMPTS`) would
    then skip the row forever. One call, one attempt.
    """
    settings_attr = TEMPLATE_ID_MAP.get(notification.template_name)
    template_id = getattr(settings, settings_attr, None) if settings_attr else None

    if not template_id:
        notification.status = NotificationStatus.FAILED
        notification.error_message = (
            f"No Brevo template ID configured for template_name="
            f"'{notification.template_name}' (expected settings.{settings_attr})."
        )
        notification.attempt_count += 1
        notification.save(update_fields=["status", "error_message", "attempt_count", "updated_at"])
        return False

    try:
        _send_via_brevo(
            to_email=notification.recipient_email,
            template_id=template_id,
            params=notification.context,
            template_name=notification.template_name,
        )
    except Exception as exc:
        transitioned = ServiceHealthState.record_failure(BREVO_SERVICE, str(exc))
        now_down = ServiceHealthState.is_down(BREVO_SERVICE)
        notification.status = NotificationStatus.FAILED
        notification.error_message = str(exc)
        # Don't burn the retry budget on failures that are part of a known Brevo
        # outage — otherwise a multi-hour outage marches every queued email to
        # attempt_count == MAX and strands it. During an outage the row stays
        # FAILED with attempts remaining, so the retry sweep + the
        # drain-on-recovery keep re-trying it until Brevo is back.
        if not now_down:
            notification.attempt_count += 1
        notification.save(update_fields=["status", "error_message", "attempt_count", "updated_at"])
        logger.warning(
            "Brevo send failed",
            extra={
                "event": "brevo_send_failed",
                "notification_id": str(notification.id),
                "template_name": notification.template_name,
                "attempt_count": notification.attempt_count,
            },
        )
        if transitioned:
            _emit_brevo_outage({"notification_id": str(notification.id)})
        return False

    recovered = ServiceHealthState.record_success(BREVO_SERVICE)
    notification.status = NotificationStatus.SENT
    notification.sent_at = timezone.now()
    notification.attempt_count += 1
    fields = ["status", "sent_at", "attempt_count", "updated_at"]
    # SENT is terminal, so the credential in `context` has served its only
    # purpose — it has just been handed to Brevo. Redact it here rather than at
    # cleanup time: the alternative left a plaintext temporary password readable
    # in the admin and in every database backup for 90 days. Deliberately NOT
    # done on a FAILED row, because the retry sweep re-reads `context` to
    # re-send; the scrub happens again wherever a row goes ABANDONED.
    if notification.template_name in AUTH_SECRET_TEMPLATES:
        notification.context = dict(REDACTED_CONTEXT)
        fields.append("context")
    notification.save(update_fields=fields)
    if recovered:
        _emit_brevo_recovered()
        _drain_after_recovery()
    return True


# The Brevo SDK client, built once. See _brevo_api().
_brevo_api_client = None
_brevo_api_lock = threading.Lock()


def _brevo_api():
    """
    The shared Brevo TransactionalEmailsApi.

    Built once rather than per send. `Configuration()`, `ApiClient()` and
    `TransactionalEmailsApi()` used to be constructed inside every call, which
    also meant a fresh `urllib3.PoolManager` — so every email paid for a new TLS
    handshake to api.brevo.com and the connection pool never got reused. Cheap
    per send, but it is per-thread churn in a pool that exists to send mail.

    **Thread safety**, since this is now shared across the background pool: the
    SDK's transport is a `urllib3.PoolManager`, which is thread-safe by design,
    and the object's own state (`default_headers`, `configuration`) is written at
    construction and only read afterwards. `ApiClient` does lazily create a
    `multiprocessing.pool.ThreadPool`, but only to service `async_req=True`
    calls; every call here is synchronous, so it is never created.

    `connection_pool_maxsize` is pinned to the background pool plus headroom. The
    SDK's own default is `multiprocessing.cpu_count() * 5`, which is a number
    about the machine rather than about how many senders we actually run — and
    inside a container `cpu_count()` reports the host's CPUs, not the share we
    were allocated. Deriving it from BACKGROUND_MAX_WORKERS (plus room for
    request threads that fell back to inline under backpressure) makes it
    predictable and keeps urllib3 from discarding connections with a
    "Connection pool is full" warning.

    The API key is read once here, so rotating BREVO_API_KEY needs a restart —
    already true of every other setting.
    """
    global _brevo_api_client
    if _brevo_api_client is None:
        with _brevo_api_lock:
            if _brevo_api_client is None:
                import sib_api_v3_sdk

                configuration = sib_api_v3_sdk.Configuration()
                configuration.api_key["api-key"] = settings.BREVO_API_KEY
                configuration.connection_pool_maxsize = (
                    getattr(settings, "BACKGROUND_MAX_WORKERS", 4) + 4
                )
                _brevo_api_client = sib_api_v3_sdk.TransactionalEmailsApi(
                    sib_api_v3_sdk.ApiClient(configuration)
                )
    return _brevo_api_client


def reset_brevo_client() -> None:
    """Drop the cached client. For tests, and for a shell after changing the key."""
    global _brevo_api_client
    with _brevo_api_lock:
        _brevo_api_client = None


def _sender_overrides(template_name: str | None = None) -> dict:
    """
    The `sender` / `reply_to` kwargs for one send, from settings.

    Resolution is three-tiered, most specific first:

    1. ``settings.BREVO_SENDER_OVERRIDES[template_name]`` — this template's own
       identity, for the one template that should not share the platform's.
    2. ``BREVO_SENDER_EMAIL`` / ``BREVO_SENDER_NAME`` / ``BREVO_REPLY_TO_EMAIL``
       — the platform default.
    3. Nothing sent at all, leaving the Brevo template's own configured sender.

    Each key is OMITTED when it resolves to blank, rather than passed as None:
    an absent field is what makes Brevo fall back to the template's sender,
    which is the documented no-op and the behaviour this project had before any
    of these settings existed. Omitting also keeps a blank env var from ever
    being serialised into the request as an explicit null.

    The sender email and name resolve TOGETHER as one identity — an override
    that sets an address uses its own name (blank meaning "no display name")
    and never inherits BREVO_SENDER_NAME, because a per-field fallback produces
    "Client Support <alerts@...>". reply_to resolves independently: where
    replies go is a separate question from who the mail is from.

    Read from settings at call time, not at import, so changing an address needs
    only a restart — and so @override_settings works in tests.
    """
    override = settings.BREVO_SENDER_OVERRIDES.get(template_name or "", {})

    if override.get("email"):
        sender_email = override["email"]
        sender_name = override.get("name", "")
    else:
        sender_email = settings.BREVO_SENDER_EMAIL
        sender_name = settings.BREVO_SENDER_NAME

    reply_to_email = override.get("reply_to") or settings.BREVO_REPLY_TO_EMAIL

    overrides: dict = {}

    if sender_email:
        sender = {"email": sender_email}
        if sender_name:
            sender["name"] = sender_name
        overrides["sender"] = sender

    if reply_to_email:
        # No name: mail clients don't surface a Reply-To display name the way
        # they do a From name, and one fewer setting is one fewer thing to drift.
        overrides["reply_to"] = {"email": reply_to_email}

    return overrides


def _send_via_brevo(
    to_email: str, template_id: int, params: dict, template_name: str | None = None
) -> None:
    """
    Send using a Brevo saved template ID. `params` maps to Brevo's template
    variables, referenced in the Brevo editor as {{ params.title }} etc.
    Raises on failure — send_now() is the only caller and handles it.

    `template_name` is the NotificationType value, carried alongside the numeric
    id purely so _sender_overrides can look up a per-template identity. It is
    keyed on the name rather than the id because Brevo ids are account-scoped
    and differ between environments; it defaults to None so the platform default
    still applies to any caller that does not have one.
    """
    if settings.TESTING:
        # Never make a real network call to Brevo during tests — mirrors
        # Django's old locmem email backend: a silent successful no-op, so
        # code paths that trigger a notification as a side effect (creating a
        # reminder, marking a milestone paid, ...) don't fail their own tests
        # over an unrelated external API call (BACKGROUND_EAGER=True under test
        # means queue_notification runs send_now synchronously).
        # apps/notifications/tests.py verifies the actual Brevo call itself
        # by mocking this function directly, which bypasses this guard
        # entirely (the mock replaces the whole function body).
        return

    import sib_api_v3_sdk
    from sib_api_v3_sdk.rest import ApiException

    send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": to_email}],
        template_id=int(template_id),
        params=params,
        **_sender_overrides(template_name),
    )

    try:
        _brevo_api().send_transac_email(
            send_smtp_email, _request_timeout=BREVO_REQUEST_TIMEOUT
        )
    except ApiException as exc:
        raise RuntimeError(f"Brevo send error {exc.status}: {exc.body}") from exc


# ── Context serialization ────────────────────────────────────────
#
# Notification.context is a JSONField, and the same dict becomes Brevo's
# `params` — both need JSON-safe primitives. Centralizing this here means
# every queue_notification() caller can pass Decimal/date/UUID values
# straight through instead of hand-casting str(...) at each call site.

def _serialise_context(context: dict) -> dict:
    return {k: _serialise_value(v) for k, v in context.items()}


def _serialise_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_serialise_value(item) for item in value]
    if isinstance(value, dict):
        return {k: _serialise_value(v) for k, v in value.items()}
    return str(value)
