"""
apps/notifications/models.py

Delivery record for every email the platform sends (reminders, payment-due
digests, meeting-prep digests, ...). Every app that wants to email a client
goes through notifications.services.queue_notification() rather than calling
Brevo's API directly — that's what makes retries, a failure audit trail, and
the cleanup sweep possible in one place instead of N ad-hoc copies. Delivery
itself goes through Brevo's transactional email API (services.send_now) —
every template_name below maps to a Brevo dashboard template ID via a
BREVO_TEMPLATE_* setting, see apps/notifications/README.md.
"""

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import UUIDTimestampedModel


class NotificationStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    # Terminal. FAILED means "will be retried"; ABANDONED means "we have stopped
    # trying" — either the attempt budget ran out or the row sat undeliverable
    # past the give-up window. The hourly sweep only looks at FAILED, so an
    # ABANDONED row is never picked up again. It is kept (not deleted) so the
    # failure stays queryable in the admin instead of vanishing silently.
    ABANDONED = "abandoned", "Abandoned"


class Notification(UUIDTimestampedModel):
    recipient_email = models.EmailField()
    recipient_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
    )
    # Nullable, not because notifications can be un-scoped in practice, but
    # because not every future notification type will necessarily be tied to
    # one engagement (e.g. a staff-facing alert) — keep the model generic.
    engagement = models.ForeignKey(
        "portal.EventEngagement", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="notifications",
    )

    template_name = models.CharField(max_length=100)  # NotificationType value -> a Brevo template ID
    subject = models.CharField(max_length=255)
    context = models.JSONField(default=dict)  # template variables, e.g. {"reminder_title": "...", "due_date": "..."}

    status = models.CharField(max_length=20, choices=NotificationStatus.choices, default=NotificationStatus.QUEUED)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    attempt_count = models.IntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["recipient_email"]),
        ]

    def __str__(self) -> str:
        return f"{self.template_name} -> {self.recipient_email} [{self.status}]"


# Every value here must have a matching BREVO_TEMPLATE_<NAME> setting
# (config/settings.py) and is the validation source queue_notification()
# checks template_name against — not just a presentational admin dropdown
# list anymore, since notification_contexts.RENDERERS no longer exists.
class NotificationType(models.TextChoices):
    NEW_REMINDER = "new_reminder", "New reminder"
    PAYMENT_DUE = "payment_due", "Payment due"
    MEETING_PREP_DUE = "meeting_prep_due", "Meeting prep due"
    PHASE_ADVANCED = "phase_advanced", "Planning phase advanced"
    EVENT_DETAILS_UPDATED = "event_details_updated", "Event details updated"
    DOCUMENT_ADDED = "document_added", "Document added"
    INVOICE_ISSUED = "invoice_issued", "Invoice issued"
    RECEIPT_ISSUED = "receipt_issued", "Receipt issued"
    MILESTONE_PAID = "milestone_paid", "Payment milestone paid"
    USER_CREDENTIALS = "user_credentials", "User credentials"
    PASSWORD_RESET = "password_reset", "Password reset"


class NotificationTypeSettings(models.Model):
    """
    Per-notification-type on/off switch, configured in the Django admin (no
    API) — e.g. turn off "payment due" reminders without silencing everything
    else. One row per `template_name` (a NotificationType value).

    A template with NO row here is treated as enabled (fail-open) — see
    is_enabled() — so a newly added NotificationType keeps working the
    moment it's added, before anyone remembers to add a settings row for it.
    """
    template_name = models.CharField(max_length=100, unique=True, choices=NotificationType.choices)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Notification Type Setting"
        verbose_name_plural = "Notification Type Settings"
        ordering = ["template_name"]

    def __str__(self) -> str:
        return f"{self.template_name} ({'on' if self.enabled else 'off'})"

    @classmethod
    def is_enabled(cls, template_name: str) -> bool:
        row = cls.objects.filter(template_name=template_name).first()
        return True if row is None else row.enabled


class ScheduledTaskSettings(models.Model):
    """
    Admin on/off switch for a background/periodic Celery task, independent of
    any per-notification-type gate (NotificationTypeSettings) — whether this
    specific job runs at all (e.g. pause the payment-due digest without
    touching the beat schedule or redeploying). Fail-open: a task_key with no
    row runs normally, same reasoning as NotificationTypeSettings, so a newly
    added task works immediately before anyone remembers to seed a row for it.

    Deliberately NOT applied to notifications.tasks.send_notification_task
    (the low-level "send this one already-approved, already-queued
    Notification" task) — gating it would strand already-queued rows in
    status=queued forever rather than stop anything meaningful. The correct
    control point for "should this notification exist at all" is already
    NotificationTypeSettings, checked before the row is even created.
    """
    task_key = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Scheduled Task Setting"
        verbose_name_plural = "Scheduled Task Settings"
        ordering = ["label"]

    def __str__(self) -> str:
        return f"{self.label} [{'on' if self.is_enabled else 'off'}]"

    @classmethod
    def is_task_enabled(cls, task_key: str) -> bool:
        row = cls.objects.filter(task_key=task_key).only("is_enabled").first()
        return True if row is None else row.is_enabled


class ServiceStatus(models.TextChoices):
    UP = "up", "Up"
    DOWN = "down", "Down"
    UNKNOWN = "unknown", "Unknown"


class ServiceHealthState(models.Model):
    """
    Current up/down health of an external dependency (today just Brevo), updated
    from two places — the real send path (passive) and the active probe — so an
    outage is caught before an email is even sent. See apps/notifications/README.md
    and docs/OBSERVABILITY_STANDARD.md.

    Two jobs in one row:
      1. Admin-visible "is Brevo up?" status (this is a plain admin table).
      2. The alert-dedup mechanism: callers emit the high-severity
         `brevo_outage` / `brevo_recovered` signal ONLY on a status *transition*
         (record_failure/record_success return whether one happened), so a
         multi-hour outage produces exactly one alert, not one per failed send.

    A row per service is seeded by migration (service="brevo"), so the
    select_for_update() in the recorders always locks an existing row and stays
    race-safe across concurrent workers.
    """
    service = models.CharField(max_length=50, unique=True)
    status = models.CharField(
        max_length=10, choices=ServiceStatus.choices, default=ServiceStatus.UNKNOWN
    )
    consecutive_failures = models.IntegerField(default=0)
    last_ok_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Consecutive failures before the passive (real-send) path declares an
    # outage. The probe passes a lower threshold — it's a deliberate health
    # check, so it shouldn't need three misses to react.
    FAILURE_THRESHOLD = 3

    class Meta:
        verbose_name = "Service Health State"
        verbose_name_plural = "Service Health States"
        ordering = ["service"]

    def __str__(self) -> str:
        return f"{self.service}: {self.status}"

    @classmethod
    def record_failure(cls, service: str, error: str = "", threshold: int | None = None) -> bool:
        """Record a failed interaction. Returns True only on an up/unknown -> down
        transition (the caller should emit the single `*_outage` escalation then)."""
        threshold = threshold or cls.FAILURE_THRESHOLD
        with transaction.atomic():
            state, _ = cls.objects.select_for_update().get_or_create(service=service)
            state.consecutive_failures += 1
            state.last_failure_at = timezone.now()
            state.last_error = (error or "")[:2000]
            transitioned = False
            if state.consecutive_failures >= threshold and state.status != ServiceStatus.DOWN:
                state.status = ServiceStatus.DOWN
                transitioned = True
            state.save()
            return transitioned

    @classmethod
    def record_success(cls, service: str) -> bool:
        """Record a healthy interaction. Returns True only on a down -> up
        transition (the caller should emit `*_recovered` and drain the backlog)."""
        with transaction.atomic():
            state, _ = cls.objects.select_for_update().get_or_create(service=service)
            was_down = state.status == ServiceStatus.DOWN
            state.consecutive_failures = 0
            state.last_ok_at = timezone.now()
            state.last_error = ""
            state.status = ServiceStatus.UP
            state.save()
            return was_down

    @classmethod
    def is_down(cls, service: str) -> bool:
        row = cls.objects.filter(service=service).only("status").first()
        return row is not None and row.status == ServiceStatus.DOWN
