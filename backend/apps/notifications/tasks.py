"""
apps/notifications/tasks.py

send_notification_task           — single send, retries up to 3x with exponential backoff
retry_failed_notifications_task  — hourly sweep for stuck failed notifications
cleanup_old_notifications_task   — weekly purge of sent notifications > 90 days old

Mirrors the retry/backoff shape already used in apps/accounts/tasks.py, but
routed through a durable Notification row instead of a fire-and-forget task,
so failures are queryable (admin, support) rather than only visible in logs.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
# Flat wait between retries — deliberately NOT exponential backoff. Three tries
# five minutes apart is easy to reason about; a growing delay just makes "when
# does it stop?" impossible to answer at a glance.
RETRY_DELAY_SECONDS = 300
# Absolute give-up window for a row that never even got a real attempt. The
# outage path deliberately doesn't spend the attempt budget (see send_now), which
# is right for a blip but would otherwise let a row be re-queued hourly forever
# if Brevo never came back. After this long, stop: mark it ABANDONED.
GIVE_UP_AFTER_DAYS = 7


@shared_task(
    bind=True,
    max_retries=MAX_ATTEMPTS - 1,       # first attempt + 2 retries = 3 total
    default_retry_delay=60,             # base seconds; multiplied below
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_notification_task(self, notification_id: str, force: bool = False) -> None:
    """Attempt to send a single Notification. Retries with exponential backoff on failure.

    ``force`` bypasses the Brevo circuit breaker. Normal enqueues leave it False,
    so while Brevo is known-down they park instead of hammering a dead API. The
    hourly retry sweep and the drain-on-recovery pass force=True, so they genuinely
    re-attempt (half-open) and thus detect recovery even if the active probe is off.
    """
    from apps.notifications.models import Notification, NotificationStatus, ServiceHealthState
    from apps.notifications.services import BREVO_SERVICE, send_now

    try:
        notification = Notification.objects.get(id=notification_id)
    except Notification.DoesNotExist:
        logger.error("Notification %s not found", notification_id)
        return

    if notification.status == NotificationStatus.SENT:
        return  # idempotent guard

    # Circuit breaker: Brevo is known-down and this is a normal delivery — park
    # it rather than hammer a dead API and burn the retry budget. Leave it
    # FAILED-with-attempts-remaining so the hourly sweep + drain-on-recovery
    # re-attempt it; do NOT self.retry (that would exhaust max_retries mid-outage).
    if not force and ServiceHealthState.is_down(BREVO_SERVICE):
        notification.status = NotificationStatus.FAILED
        notification.error_message = "Deferred: Brevo is currently unavailable."
        notification.save(update_fields=["status", "error_message", "updated_at"])
        logger.info(
            "Brevo down; deferring notification",
            extra={"event": "brevo_send_deferred", "notification_id": notification_id},
        )
        return

    success = send_now(notification)

    if not success:
        # During a known outage, skip the fast retry storm — the hourly sweep +
        # drain re-deliver on a calm cadence once Brevo is back.
        if ServiceHealthState.is_down(BREVO_SERVICE):
            return

        retry_number = self.request.retries          # 0-indexed
        attempts_so_far = retry_number + 1

        if attempts_so_far < MAX_ATTEMPTS:
            logger.warning(
                "notification send failed, retrying in %ss (attempt %s/%s)",
                RETRY_DELAY_SECONDS, attempts_so_far, MAX_ATTEMPTS,
                extra={"notification_id": notification_id},
            )
            raise self.retry(countdown=RETRY_DELAY_SECONDS)

        # Budget spent — stop for good. ABANDONED is terminal, so the hourly
        # sweep (which only scans FAILED) will never pick this row up again.
        notification.refresh_from_db(fields=["attempt_count"])
        notification.status = NotificationStatus.ABANDONED
        notification.save(update_fields=["status", "updated_at"])
        logger.error(
            "notification abandoned after %s attempts — no further retries",
            MAX_ATTEMPTS,
            extra={"notification_id": notification_id},
        )


@shared_task(name="notifications.retry_failed")
def retry_failed_notifications_task() -> None:
    """
    Hourly sweep: re-queue FAILED notifications that still have attempts left.

    Two hard stops, so nothing is retried forever:
      * `attempt_count >= MAX_ATTEMPTS` — the budget is spent (the row is marked
        ABANDONED by send_notification_task at that point).
      * older than GIVE_UP_AFTER_DAYS — covers the one case the attempt budget
        can't: a row parked by the Brevo-outage path never spends an attempt, so
        without an age ceiling it would be re-queued every hour indefinitely if
        Brevo never recovered. Those are marked ABANDONED here.

    Skips per-type: a FAILED row whose template_name is currently disabled
    (NotificationTypeSettings) is left alone rather than resent — bypassing
    queue_notification here means this sweep must apply that gate itself.
    """
    from apps.notifications.models import Notification, NotificationStatus, NotificationTypeSettings, ScheduledTaskSettings

    if not ScheduledTaskSettings.is_task_enabled("notifications_retry_failed"):
        return

    failed = Notification.objects.filter(status=NotificationStatus.FAILED)
    cutoff = timezone.now() - timedelta(days=GIVE_UP_AFTER_DAYS)

    # Stop chasing anything past the give-up window, whatever its attempt count.
    stale = failed.filter(created_at__lt=cutoff)
    given_up = stale.update(status=NotificationStatus.ABANDONED)
    if given_up:
        logger.warning(
            "abandoned %s notification(s) still undelivered after %s days",
            given_up, GIVE_UP_AFTER_DAYS,
        )

    candidates = failed.filter(attempt_count__lt=MAX_ATTEMPTS, created_at__gte=cutoff)
    for notification in candidates:
        if NotificationTypeSettings.is_enabled(notification.template_name):
            # force=True: the sweep genuinely re-attempts even while the breaker
            # is open, so it both drains the backlog and detects Brevo recovery.
            send_notification_task.delay(str(notification.id), force=True)


@shared_task(name="notifications.cleanup_old")
def cleanup_old_notifications_task() -> None:
    """Weekly purge: delete sent notifications older than 90 days."""
    from apps.notifications.models import Notification, NotificationStatus, ScheduledTaskSettings

    if not ScheduledTaskSettings.is_task_enabled("notifications_cleanup_old"):
        return

    cutoff = timezone.now() - timedelta(days=90)
    Notification.objects.filter(status=NotificationStatus.SENT, sent_at__lt=cutoff).delete()


def _check_brevo_reachable() -> tuple[bool, str]:
    """Is Brevo's transactional API reachable right now? Returns (ok, detail).

    Primary signal is the account endpoint — the same host/auth real sends use.
    A 401/403 means our key/account is the problem, NOT a Brevo outage, so we
    report reachable to avoid false-alarming on a misconfigured key. On a
    connection/5xx failure we corroborate with Brevo's public status page and
    fold its indicator into the detail for a richer alert.
    """
    import requests
    from django.conf import settings
    from apps.notifications.services import BREVO_REQUEST_TIMEOUT

    try:
        resp = requests.get(
            "https://api.brevo.com/v3/account",
            headers={"api-key": settings.BREVO_API_KEY, "accept": "application/json"},
            timeout=BREVO_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return False, f"account endpoint unreachable: {exc}{_status_page_note()}"

    if resp.status_code == 200:
        return True, "account endpoint 200"
    if resp.status_code in (401, 403):
        return True, f"reachable (auth {resp.status_code} — key/account issue, not an outage)"
    return False, f"account endpoint HTTP {resp.status_code}{_status_page_note()}"


def _status_page_note() -> str:
    """Best-effort read of status.brevo.com's overall indicator, for context in
    the alert. Never raises — a status-page failure must not change the verdict."""
    import requests
    try:
        r = requests.get("https://status.brevo.com/api/v2/status.json", timeout=5)
        indicator = r.json().get("status", {}).get("indicator", "unknown")
        return f"; statuspage indicator={indicator}"
    except Exception:
        return "; statuspage unavailable"


@shared_task(name="notifications.brevo_health_probe")
def brevo_health_probe_task() -> None:
    """Active Brevo reachability probe (beat schedule, ~every 5 min) — catches an
    outage before an email is even sent. Admin-gated via ScheduledTaskSettings;
    its schedule is an admin-editable PeriodicTask (see seed_periodic_tasks)."""
    from apps.notifications.models import ScheduledTaskSettings, ServiceHealthState
    from apps.notifications.services import (
        BREVO_SERVICE,
        _drain_after_recovery,
        _emit_brevo_outage,
        _emit_brevo_recovered,
    )

    if not ScheduledTaskSettings.is_task_enabled("notifications_brevo_health_probe"):
        return

    reachable, detail = _check_brevo_reachable()

    if reachable:
        if ServiceHealthState.record_success(BREVO_SERVICE):  # down -> up transition
            _emit_brevo_recovered()
            _drain_after_recovery()
    else:
        # The probe is a deliberate health check — react after 2 consecutive
        # misses (vs the passive path's 3), but still absorb a one-off blip.
        if ServiceHealthState.record_failure(BREVO_SERVICE, detail, threshold=2):
            _emit_brevo_outage({"source": "probe", "detail": detail})
