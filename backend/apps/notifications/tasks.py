"""
apps/notifications/tasks.py

send_notification_task           — single send, one attempt per dispatch
retry_failed_notifications_task  — the sweep: re-drives FAILED *and* stranded
                                   QUEUED rows (cron group `notification_retry`)
cleanup_old_notifications_task   — weekly purge of terminal rows > 90 days old

Delivery is routed through a durable Notification row rather than a
fire-and-forget job, so failures are queryable (admin, support) rather than only
visible in logs. That durable row is now load-bearing rather than merely nice:
there is no broker, so if the web process dies mid-send the row is the *only*
record that the send was ever wanted. See docs/adr/0001-remove-celery.md.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from apps.core.background import background_task

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
# Absolute give-up window for a row that never even got a real attempt. The
# outage path deliberately doesn't spend the attempt budget (see send_now), which
# is right for a blip but would otherwise let a row be re-queued forever if Brevo
# never came back. After this long, stop: mark it ABANDONED.
GIVE_UP_AFTER_DAYS = 7
# How long a row may sit in QUEUED before the sweep assumes its in-process
# dispatch was lost and re-drives it. See the docstring on the sweep — this is
# the hole the broker used to plug, and closing it is what makes a broker-less
# dispatch safe.
STRANDED_AFTER = timedelta(minutes=10)
# Templates whose content expires. A password-reset code is dead 30 minutes
# after it is minted (apps/accounts/utils.create_password_reset_token), and a
# credentials email is the user's only way in — so the sweep re-drives these
# before anything else rather than working through a flat queue of digests.
PRIORITY_TEMPLATES = ("password_reset", "user_credentials")
# Non-terminal statuses the sweep re-drives unconditionally. QUEUED is handled
# separately because it needs an age floor (see STRANDED_AFTER); these two do not
# — a FAILED or DEFERRED row has already had its turn.
# Values, not enum members, because this module deliberately imports models
# lazily inside each task (they are imported at call time, not module import
# time, so the tasks can be imported before the app registry is ready).
RETRYABLE_STATUSES = ("failed", "deferred")


@background_task(name="notifications.send")
def send_notification_task(notification_id: str, force: bool = False) -> None:
    """Attempt to send a single Notification. Exactly ONE attempt per dispatch.

    There is deliberately no in-task retry. The old `self.retry(countdown=300)`
    could not survive the move off Celery, and not only for want of a broker:
    `send_now()` increments `attempt_count` on every call, so an in-thread retry
    loop would burn all three attempts inside one dispatch — and the sweep, which
    filters `attempt_count__lt=MAX_ATTEMPTS`, would then skip the row forever.

    So the cron sweep (`retry_failed_notifications_task`, group
    `notification_retry`) is the one and only retry path. Its cadence is
    therefore load-bearing, not cosmetic: it is the ceiling on how long a
    password-reset email can be delayed.

    ``force`` bypasses the Brevo circuit breaker. Normal enqueues leave it False,
    so while Brevo is known-down they park instead of hammering a dead API. The
    sweep and the drain-on-recovery pass force=True, so they genuinely
    re-attempt (half-open) and thus detect recovery.
    """
    from apps.notifications.models import Notification, NotificationStatus, ServiceHealthState
    from apps.notifications.services import (
        AUTH_SECRET_TEMPLATES,
        BREVO_SERVICE,
        REDACTED_CONTEXT,
        send_now,
    )

    try:
        notification = Notification.objects.get(id=notification_id)
    except Notification.DoesNotExist:
        logger.error("Notification %s not found", notification_id)
        return

    if notification.status == NotificationStatus.SENT:
        return  # idempotent guard

    # Circuit breaker: Brevo is known-down and this is a normal delivery — park
    # it rather than hammer a dead API and burn the retry budget. DEFERRED, not
    # FAILED: nothing was attempted, attempt_count stays 0, and the sweep +
    # drain-on-recovery re-attempt it exactly as they would a FAILED row.
    if not force and ServiceHealthState.is_down(BREVO_SERVICE):
        notification.status = NotificationStatus.DEFERRED
        notification.error_message = "Deferred: Brevo is currently unavailable."
        notification.save(update_fields=["status", "error_message", "updated_at"])
        logger.info(
            "Brevo down; deferring notification",
            extra={"event": "brevo_send_deferred", "notification_id": notification_id},
        )
        return

    if send_now(notification):
        return

    # send_now() already recorded FAILED, the error, and the attempt (unless the
    # send failed as part of a known outage, which deliberately spends no
    # attempt). During a known outage there is nothing more to decide — the
    # sweep keeps re-trying on a calm cadence once Brevo is back.
    if ServiceHealthState.is_down(BREVO_SERVICE):
        return

    if notification.attempt_count >= MAX_ATTEMPTS:
        # Budget spent — stop for good. ABANDONED is terminal, so the sweep will
        # never pick this row up again.
        notification.status = NotificationStatus.ABANDONED
        fields = ["status", "updated_at"]
        # Terminal, so nothing will ever re-read `context` — and an abandoned
        # credentials email is precisely the row that used to keep a plaintext
        # temporary password forever, since the weekly cleanup only touched SENT.
        if notification.template_name in AUTH_SECRET_TEMPLATES:
            notification.context = dict(REDACTED_CONTEXT)
            fields.append("context")
        notification.save(update_fields=fields)
        logger.error(
            "notification abandoned after %s attempts — no further retries",
            MAX_ATTEMPTS,
            extra={"notification_id": notification_id},
        )
    else:
        logger.warning(
            "notification send failed (attempt %s/%s) — the retry sweep will re-drive it",
            notification.attempt_count, MAX_ATTEMPTS,
            extra={"notification_id": notification_id},
        )


@background_task(name="notifications.retry_failed")
def retry_failed_notifications_task() -> None:
    """
    The recovery sweep. Runs from cron group `notification_retry`, and is the
    ONLY retry path — there is no in-task retry any more (see
    send_notification_task).

    It re-drives three populations:

    **FAILED** — a send that was genuinely attempted and failed.

    **DEFERRED** — a send that was never attempted because the Brevo breaker was
    open when its turn came. Handled identically to FAILED here; the distinction
    exists so the admin doesn't call it a failure.

    **Stranded QUEUED** — a row created more than STRANDED_AFTER ago whose
    in-process dispatch never ran. Under Celery this barely mattered: the broker
    held the message, so a worker restart redelivered it. With no broker this is
    the *primary* loss mode —

        queue_notification()  ->  Notification.objects.create(status=QUEUED)  [committed]
                              ->  on_commit -> pool.submit(...)
                              ->  [deploy SIGTERM / OOM / instance restart]

    — and without this pass that row would sit QUEUED forever, with nothing
    looking at it. Note the hole existed before the migration too, for any row
    created while the worker was down; it was simply much narrower.

    Two hard stops, so nothing is retried forever:
      * `attempt_count >= MAX_ATTEMPTS` — the budget is spent (the row is marked
        ABANDONED by send_notification_task at that point).
      * older than GIVE_UP_AFTER_DAYS — covers the one case the attempt budget
        can't: a row parked by the Brevo-outage path never spends an attempt, so
        without an age ceiling it would be re-queued forever if Brevo never
        recovered. Those are marked ABANDONED here.

    Skips per-type: a row whose template_name is currently disabled
    (NotificationTypeSettings) is left alone rather than resent — bypassing
    queue_notification here means this sweep must apply that gate itself.
    """
    from django.db.models import Case, IntegerField, Q, When

    from apps.notifications.models import (
        Notification,
        NotificationStatus,
        NotificationTypeSettings,
        ScheduledTaskSettings,
    )
    from apps.notifications.services import scrub_auth_context, sweep_in_progress

    if not ScheduledTaskSettings.is_task_enabled("notifications_retry_failed"):
        return

    now = timezone.now()
    cutoff = now - timedelta(days=GIVE_UP_AFTER_DAYS)

    # A QUEUED row only counts as stranded once its dispatch has had time to run.
    # Without the age floor this sweep would race the pool and re-drive a send
    # that is in flight right now, double-mailing the recipient.
    retryable = Q(status__in=RETRYABLE_STATUSES) | Q(
        status=NotificationStatus.QUEUED, created_at__lt=now - STRANDED_AFTER
    )

    # Stop chasing anything past the give-up window, whatever its attempt count
    # or status. A QUEUED row this old is stranded *and* stale — the reminder it
    # describes is a week out of date.
    giving_up = Notification.objects.filter(retryable, created_at__lt=cutoff)
    # Scrub before the status flips: once these are ABANDONED they are terminal
    # and nothing re-reads `context`, but the filter below matches on status, so
    # do it while it still selects them.
    scrub_auth_context(giving_up)
    given_up = giving_up.update(status=NotificationStatus.ABANDONED)
    if given_up:
        logger.warning(
            "abandoned %s notification(s) still undelivered after %s days",
            given_up, GIVE_UP_AFTER_DAYS,
        )

    candidates = (
        Notification.objects.filter(
            retryable, attempt_count__lt=MAX_ATTEMPTS, created_at__gte=cutoff
        )
        # Auth mail first. Everything here is re-driven in one pass anyway, but
        # this pass runs inline in a cron process with a finite lifetime — if it
        # is killed part-way through, the thing that survived being dropped
        # should be the digest, not the reset code that expires in 30 minutes.
        .annotate(
            priority=Case(
                *[
                    When(template_name=name, then=rank)
                    for rank, name in enumerate(PRIORITY_TEMPLATES)
                ],
                default=len(PRIORITY_TEMPLATES),
                output_field=IntegerField(),
            )
        )
        .order_by("priority", "created_at")
    )

    # sweep_in_progress: the forced sends below can be what detects Brevo coming
    # back, and that transition calls _drain_after_recovery — which is this same
    # sweep. This cycle IS the drain, so suppress the re-entry.
    with sweep_in_progress():
        for notification in candidates:
            if NotificationTypeSettings.is_enabled(notification.template_name):
                # force=True: the sweep genuinely re-attempts even while the
                # breaker is open, so it both drains the backlog and detects
                # Brevo recovery. This runs inline — async dispatch is opt-in and
                # a cron process never opts in — so the sweep sends its own mail
                # before exiting rather than handing it to a pool that dies with
                # the command.
                send_notification_task.delay(str(notification.id), force=True)


@background_task(name="notifications.cleanup_old")
def cleanup_old_notifications_task() -> None:
    """
    Weekly purge of terminal notifications older than 90 days.

    Covers SENT *and* ABANDONED. Only SENT used to be purged, which meant the
    rows most likely to hold a stack trace in `error_message` — and, before the
    scrub in services.send_now, a plaintext temporary password in `context` —
    were the ones kept forever.

    FAILED and DEFERRED are deliberately left alone: the retry sweep still owns
    those, and it converts anything past the give-up window to ABANDONED, so a
    genuinely dead row always ends up in this net within a week.
    """
    from apps.notifications.models import Notification, NotificationStatus, ScheduledTaskSettings

    if not ScheduledTaskSettings.is_task_enabled("notifications_cleanup_old"):
        return

    cutoff = timezone.now() - timedelta(days=90)

    sent, _ = Notification.objects.filter(
        status=NotificationStatus.SENT, sent_at__lt=cutoff
    ).delete()
    # ABANDONED rows may never have a sent_at, so age them off created_at.
    abandoned, _ = Notification.objects.filter(
        status=NotificationStatus.ABANDONED, created_at__lt=cutoff
    ).delete()

    if sent or abandoned:
        logger.info(
            "purged old notifications",
            extra={"event": "notifications_purged", "sent": sent, "abandoned": abandoned},
        )
