"""
apps/notifications/services.py

All outbound email goes through queue_notification() -> Celery
(tasks.send_notification_task) -> send_now() -> Brevo's transactional email
API. See apps/notifications/README.md for the full template list and each
one's expected params.
"""

from __future__ import annotations

import datetime
import logging
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
# request can pin a Celery worker indefinitely.
BREVO_REQUEST_TIMEOUT = 10

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


def _drain_after_recovery() -> None:
    """On recovery, immediately re-queue the backlog rather than waiting for the
    hourly sweep. Reuses the same re-queue logic (retry_failed sweep)."""
    from .tasks import retry_failed_notifications_task
    retry_failed_notifications_task.delay()

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
    Create a Notification row and hand it to Celery. This is the one entry
    point every other app should call to email a client — never call Brevo's
    API directly from a view or another app's service.

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
    success, False on failure (the caller — send_notification_task — decides
    whether to retry). Never raises: any exception from the API call is
    caught and recorded on the notification itself.
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
        )
    except Exception as exc:
        transitioned = ServiceHealthState.record_failure(BREVO_SERVICE, str(exc))
        now_down = ServiceHealthState.is_down(BREVO_SERVICE)
        notification.status = NotificationStatus.FAILED
        notification.error_message = str(exc)
        # Don't burn the retry budget on failures that are part of a known Brevo
        # outage — otherwise a multi-hour outage marches every queued email to
        # attempt_count == MAX and strands it. During an outage the row stays
        # FAILED with attempts remaining, so the hourly sweep + the
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
    notification.save(update_fields=["status", "sent_at", "attempt_count", "updated_at"])
    if recovered:
        _emit_brevo_recovered()
        _drain_after_recovery()
    return True


def _send_via_brevo(to_email: str, template_id: int, params: dict) -> None:
    """
    Send using a Brevo saved template ID. `params` maps to Brevo's template
    variables, referenced in the Brevo editor as {{ params.title }} etc.
    Raises on failure — send_now() is the only caller and handles it.
    """
    if settings.TESTING:
        # Never make a real network call to Brevo during tests — mirrors
        # Django's old locmem email backend: a silent successful no-op, so
        # code paths that trigger a notification as a side effect (creating a
        # reminder, marking a milestone paid, ...) don't fail their own tests
        # over an unrelated external API call (CELERY_TASK_ALWAYS_EAGER=True
        # under test means queue_notification runs send_now synchronously).
        # apps/notifications/tests.py verifies the actual Brevo call itself
        # by mocking this function directly, which bypasses this guard
        # entirely (the mock replaces the whole function body).
        return

    import sib_api_v3_sdk
    from sib_api_v3_sdk.rest import ApiException

    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = settings.BREVO_API_KEY

    api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))
    send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": to_email}],
        template_id=int(template_id),
        params=params,
    )

    try:
        api.send_transac_email(send_smtp_email, _request_timeout=BREVO_REQUEST_TIMEOUT)
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
