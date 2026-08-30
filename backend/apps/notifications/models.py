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

from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import UUIDTimestampedModel


class NotificationStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    # Never attempted, on purpose: the Brevo circuit breaker was open when this
    # row's turn came, so it was parked rather than thrown at a dead API.
    #
    # Its own status because the alternative was lying to staff. These rows used
    # to be FAILED with error_message="Deferred: Brevo is currently unavailable."
    # and attempt_count=0 — correct behaviour under a label that told whoever was
    # reading the admin that a send had been tried and had failed, when nothing
    # had been tried at all. The retry sweep treats DEFERRED and FAILED
    # identically (both are re-driven while attempts remain), so this is purely
    # about what the admin says.
    DEFERRED = "deferred", "Deferred (service down)"
    # Terminal. FAILED and DEFERRED mean "will be retried"; ABANDONED means "we
    # have stopped trying" — either the attempt budget ran out or the row sat
    # undeliverable past the give-up window. The retry sweep re-drives FAILED,
    # DEFERRED and stranded QUEUED rows only, so an ABANDONED row is never picked
    # up again. It is kept (not deleted) for 90 days so the failure stays
    # queryable in the admin instead of vanishing silently.
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
    INQUIRY_RECEIVED = "inquiry_received", "Inquiry received"
    INQUIRY_SUBMITTED_INTERNAL = "inquiry_submitted_internal", "New inquiry submitted"


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
    Admin on/off switch for a background/scheduled task, independent of any
    per-notification-type gate (NotificationTypeSettings) — whether this specific
    job runs at all (e.g. pause the payment-due digest without touching the cron
    schedule or redeploying). Fail-open: a task_key with no row runs normally,
    same reasoning as NotificationTypeSettings, so a newly added task works
    immediately before anyone remembers to seed a row for it.

    This survived the removal of Celery unchanged, and is now the *only*
    admin-editable control over background work: every gated task still checks
    `is_task_enabled` as its first statement, whether it is invoked by
    `manage.py run_scheduled <group>` from platform cron or dispatched into the
    in-process pool. What moved out of the admin is task *timing* — that lives in
    each cron service's schedule now, not in a PeriodicTask row.

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
    from the real send path: every send outcome records into it, so a burst of
    failures trips the breaker and later sends park instead of hammering a dead
    API. See apps/notifications/README.md and docs/OBSERVABILITY_STANDARD.md.

    There used to be a second, active writer — a probe hitting Brevo's account
    endpoint every 5 minutes, which caught an outage before any email was sent.
    It was removed with Celery: 288 scheduled runs and 576 outbound HTTPS calls a
    day, to learn a few minutes earlier what the next real send would have told
    us. Detection is now purely passive, which is why DOWN_STALE_AFTER below
    matters more than it used to.

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

    # Consecutive failures before the send path declares an outage. Callers may
    # pass a lower threshold for a more deliberate signal.
    FAILURE_THRESHOLD = 3

    # How long a `down` verdict is trusted. Past this, is_down() reports False
    # and the next real send is allowed to probe Brevo itself.
    #
    # This is a deadlock guard, and a necessary one. `down` is only cleared by a
    # *successful* send, and while it is set every normal send parks itself
    # without attempting — so the only thing that can clear it is a forced send
    # from the retry sweep. If that sweep is off (ScheduledTaskSettings) or its
    # cron service is broken, a stale `down` row parks EVERY notification in the
    # platform, indefinitely, with nothing surfacing why. There is no active
    # probe to break the tie any more. Thirty minutes of parked mail during a
    # real outage is a good trade for never being permanently mute.
    DOWN_STALE_AFTER = timedelta(minutes=30)

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
        """Is this dependency known-down *right now*?

        A `down` verdict older than DOWN_STALE_AFTER is treated as unknown — see
        that constant for why. Note this only relaxes the breaker; it does not
        write the row back to `up`, so the admin still shows the last real
        verdict and the next send outcome is what actually updates it.
        """
        row = (
            cls.objects.filter(service=service)
            .only("status", "last_failure_at")
            .first()
        )
        if row is None or row.status != ServiceStatus.DOWN:
            return False

        if row.last_failure_at is None:
            # DOWN with no recorded failure can only come from a hand-edit or a
            # fixture. Don't let it park mail forever.
            return False

        return timezone.now() - row.last_failure_at < cls.DOWN_STALE_AFTER
