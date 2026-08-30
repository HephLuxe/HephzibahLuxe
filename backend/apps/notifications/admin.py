from django.contrib import admin

from .models import (
    Notification,
    NotificationStatus,
    NotificationTypeSettings,
    ScheduledTaskSettings,
    ServiceHealthState,
)


@admin.register(NotificationTypeSettings)
class NotificationTypeSettingsAdmin(admin.ModelAdmin):
    """Per-notification-type on/off switches — e.g. turn off 'payment due'
    reminders without silencing everything else. Toggle inline from the list."""
    list_display = ("template_name", "enabled", "updated_at")
    list_editable = ("enabled",)


@admin.register(ScheduledTaskSettings)
class ScheduledTaskSettingsAdmin(admin.ModelAdmin):
    """Per-background-task on/off switches — whether a scheduled/debounced job
    runs at all, independent of NotificationTypeSettings. Toggle inline from the
    list. Task *timing* is not here: it lives in each cron service's schedule
    (see apps/core/management/commands/run_scheduled.py)."""
    list_display = ("label", "task_key", "is_enabled", "updated_at")
    list_editable = ("is_enabled",)


@admin.register(ServiceHealthState)
class ServiceHealthStateAdmin(admin.ModelAdmin):
    """Live up/down health of external dependencies (e.g. Brevo). Read-only —
    the status is maintained by the outcome of real sends, not by hand. A `down`
    verdict older than ServiceHealthState.DOWN_STALE_AFTER stops blocking sends
    even though it still shows here, so a stale row can never mute the platform
    permanently."""
    list_display = ("service", "status", "consecutive_failures", "last_ok_at", "last_failure_at", "updated_at")
    list_filter = ("status",)
    readonly_fields = (
        "service", "status", "consecutive_failures",
        "last_ok_at", "last_failure_at", "last_error", "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """
    The delivery audit trail — and, with no Celery worker to inspect, the queue
    view: a row's `status` is what "is this email out yet?" means now.

    `context` is excluded, not merely read-only. It holds the exact params handed
    to Brevo, which for `password_reset` and `user_credentials` means a live
    6-digit code or a generated temporary password. services.send_now redacts it
    once a row reaches a terminal state, but a QUEUED or FAILED row legitimately
    still holds the secret (the retry sweep re-reads it to re-send), and staff
    triaging a failed send have no reason to see it.
    """
    list_display = ("recipient_email", "template_name", "status", "attempt_count", "sent_at", "created_at")
    list_filter = ("status", "template_name")
    search_fields = ("recipient_email", "subject")
    raw_id_fields = ("recipient_user", "engagement")
    exclude = ("context",)
    readonly_fields = ("created_at", "updated_at", "sent_at", "attempt_count", "error_message")
    actions = ["resend_selected"]

    @admin.action(description="Resend selected notifications")
    def resend_selected(self, request, queryset):
        from .tasks import send_notification_task

        ids = list(queryset.exclude(status=NotificationStatus.SENT).values_list("id", flat=True))
        Notification.objects.filter(id__in=ids).update(status=NotificationStatus.QUEUED)
        for notification_id in ids:
            # Dispatches into the in-process pool (this is the web process), so
            # the admin request returns without waiting on Brevo. If the process
            # dies first the rows are QUEUED and the retry sweep re-drives them.
            send_notification_task.delay(str(notification_id))
        self.message_user(request, f"Queued {len(ids)} notification(s) for resend.")
