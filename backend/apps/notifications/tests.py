"""
apps/notifications/tests.py

Run with: python manage.py test notifications
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (
    Notification,
    NotificationStatus,
    ScheduledTaskSettings,
    ServiceHealthState,
    ServiceStatus,
)
from .services import BREVO_SERVICE, queue_notification, send_now
from .tasks import brevo_health_probe_task, send_notification_task

User = get_user_model()


class NotificationServiceTests(TestCase):
    @patch("apps.notifications.services._send_via_brevo")
    def test_queue_notification_creates_row_and_dispatches_task(self, mock_send):
        # CELERY_TASK_ALWAYS_EAGER=True under `manage.py test` means .delay()
        # actually runs send_notification_task -> send_now() inline here, so
        # the Brevo call itself must be mocked to avoid a real network call.
        user = User.objects.create_user(
            first_name="T", last_name="Client", email="notif@example.com", password="x"
        )
        notification = queue_notification(
            recipient_email=user.email,
            recipient_user=user,
            template_name="new_reminder",
            context={"title": "Kickoff prep", "description": "", "priority_display": "High Priority", "due_date": None},
        )
        # Subject is now just the NotificationType label — the Brevo template
        # owns the real, per-notification subject line.
        self.assertEqual(notification.subject, "New reminder")
        mock_send.assert_called_once()
        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationStatus.SENT)

    def test_unknown_template_name_rejected(self):
        with self.assertRaises(ValueError):
            queue_notification(recipient_email="x@example.com", template_name="not_a_real_template", context={})

    @patch("apps.notifications.services._send_via_brevo")
    def test_send_now_success_calls_brevo_and_updates_status(self, mock_send):
        notification = Notification.objects.create(
            recipient_email="notif@example.com",
            template_name="payment_due",
            subject="Payment due",
            context={"label": "Deposit", "amount": "3000000", "due_date": "2026-05-06", "event_title": "Test Wedding"},
        )
        result = send_now(notification)
        self.assertTrue(result)
        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationStatus.SENT)
        self.assertIsNotNone(notification.sent_at)
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        self.assertEqual(kwargs["to_email"], "notif@example.com")
        self.assertEqual(kwargs["params"]["label"], "Deposit")

    def test_send_now_unconfigured_template_marks_failed_without_raising(self):
        notification = Notification.objects.create(
            recipient_email="notif@example.com",
            template_name="deleted_template",
            subject="x",
            context={},
        )
        result = send_now(notification)
        self.assertFalse(result)
        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationStatus.FAILED)
        self.assertEqual(notification.attempt_count, 1)
        self.assertIn("No Brevo template ID configured", notification.error_message)

    @patch("apps.notifications.services._send_via_brevo")
    def test_send_now_brevo_failure_marks_failed_without_raising(self, mock_send):
        mock_send.side_effect = RuntimeError("Brevo send error 400: bad request")
        notification = Notification.objects.create(
            recipient_email="notif@example.com",
            template_name="payment_due",
            subject="Payment due",
            context={"label": "Deposit"},
        )
        result = send_now(notification)
        self.assertFalse(result)
        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationStatus.FAILED)
        self.assertIn("Brevo send error", notification.error_message)


def _make_notification():
    return Notification.objects.create(
        recipient_email="notif@example.com",
        template_name="payment_due",
        subject="Payment due",
        context={"label": "Deposit"},
    )


class BrevoOutageDetectionTests(TestCase):
    """Passive detection (via the real send path) — one deduped escalation per
    outage on the up->down transition, recovery on the down->up transition, and
    no retry-budget burn while Brevo is known-down."""

    @patch("apps.notifications.services._emit_brevo_outage")
    @patch("apps.notifications.services._send_via_brevo")
    def test_failure_burst_flips_down_and_escalates_exactly_once(self, mock_send, mock_outage):
        mock_send.side_effect = RuntimeError("Brevo send error 500: upstream timeout")

        # Threshold is 3: the first two failures don't escalate, the third does.
        for _ in range(4):
            send_now(_make_notification())

        state = ServiceHealthState.objects.get(service=BREVO_SERVICE)
        self.assertEqual(state.status, ServiceStatus.DOWN)
        # Exactly one escalation for the whole outage, not one per failed send.
        self.assertEqual(mock_outage.call_count, 1)

    @patch("apps.notifications.services._send_via_brevo")
    def test_attempt_count_not_burned_while_down(self, mock_send):
        mock_send.side_effect = RuntimeError("Brevo send error 500: upstream timeout")
        # Trip the breaker first (3 failures across throwaway rows).
        for _ in range(3):
            send_now(_make_notification())
        self.assertTrue(ServiceHealthState.is_down(BREVO_SERVICE))

        # A fresh failing send while down must NOT increment attempt_count,
        # so the hourly sweep keeps re-trying it instead of stranding it.
        n = _make_notification()
        send_now(n)
        n.refresh_from_db()
        self.assertEqual(n.status, NotificationStatus.FAILED)
        self.assertEqual(n.attempt_count, 0)

    @patch("apps.notifications.services._drain_after_recovery")
    @patch("apps.notifications.services._emit_brevo_recovered")
    @patch("apps.notifications.services._send_via_brevo")
    def test_success_after_outage_recovers_and_drains(self, mock_send, mock_recovered, mock_drain):
        ServiceHealthState.objects.filter(service=BREVO_SERVICE).update(
            status=ServiceStatus.DOWN, consecutive_failures=3
        )
        # A clean send (mock does not raise) should flip up and drain the backlog.
        send_now(_make_notification())

        state = ServiceHealthState.objects.get(service=BREVO_SERVICE)
        self.assertEqual(state.status, ServiceStatus.UP)
        mock_recovered.assert_called_once()
        mock_drain.assert_called_once()


class BrevoBreakerTests(TestCase):
    @patch("apps.notifications.services.send_now")
    def test_send_task_defers_without_attempting_while_down(self, mock_send_now):
        ServiceHealthState.objects.filter(service=BREVO_SERVICE).update(status=ServiceStatus.DOWN)
        n = _make_notification()

        send_notification_task.delay(str(n.id))  # force defaults False

        mock_send_now.assert_not_called()  # breaker parked it, no Brevo hit
        n.refresh_from_db()
        self.assertEqual(n.status, NotificationStatus.FAILED)
        self.assertIn("Deferred", n.error_message)
        self.assertEqual(n.attempt_count, 0)


class BrevoProbeTests(TestCase):
    @patch("apps.notifications.tasks._check_brevo_reachable")
    def test_probe_respects_admin_disable(self, mock_check):
        ScheduledTaskSettings.objects.update_or_create(
            task_key="notifications_brevo_health_probe",
            defaults={"label": "probe", "is_enabled": False},
        )
        brevo_health_probe_task()
        mock_check.assert_not_called()

    @patch("apps.notifications.services._emit_brevo_outage")
    @patch("apps.notifications.tasks._check_brevo_reachable")
    def test_probe_marks_down_after_two_misses(self, mock_check, mock_outage):
        mock_check.return_value = (False, "account endpoint unreachable")
        brevo_health_probe_task()  # miss 1 — no transition yet
        self.assertNotEqual(
            ServiceHealthState.objects.get(service=BREVO_SERVICE).status, ServiceStatus.DOWN
        )
        brevo_health_probe_task()  # miss 2 — probe threshold is 2
        self.assertEqual(
            ServiceHealthState.objects.get(service=BREVO_SERVICE).status, ServiceStatus.DOWN
        )
        mock_outage.assert_called_once()

    @patch("apps.notifications.services._drain_after_recovery")
    @patch("apps.notifications.services._emit_brevo_recovered")
    @patch("apps.notifications.tasks._check_brevo_reachable")
    def test_probe_recovers_and_drains(self, mock_check, mock_recovered, mock_drain):
        ServiceHealthState.objects.filter(service=BREVO_SERVICE).update(
            status=ServiceStatus.DOWN, consecutive_failures=2
        )
        mock_check.return_value = (True, "account endpoint 200")
        brevo_health_probe_task()

        self.assertEqual(
            ServiceHealthState.objects.get(service=BREVO_SERVICE).status, ServiceStatus.UP
        )
        mock_recovered.assert_called_once()
        mock_drain.assert_called_once()


class RetryCapTests(TestCase):
    """
    A failed notification must never be retried forever. Two independent hard
    stops: the attempt budget, and the give-up window for rows parked by the
    outage path (which deliberately never spends an attempt).
    """

    def _notification(self, **kw):
        from apps.notifications.models import Notification, NotificationStatus
        defaults = dict(
            recipient_email="cap@example.com",
            template_name="new_reminder",
            subject="New reminder",
            status=NotificationStatus.FAILED,
            attempt_count=0,
        )
        defaults.update(kw)
        return Notification.objects.create(**defaults)

    @patch("apps.notifications.tasks.send_notification_task.delay")
    def test_sweep_skips_rows_that_spent_their_attempt_budget(self, mock_delay):
        from apps.notifications.tasks import MAX_ATTEMPTS, retry_failed_notifications_task
        spent = self._notification(attempt_count=MAX_ATTEMPTS)
        live = self._notification(attempt_count=MAX_ATTEMPTS - 1)

        retry_failed_notifications_task()

        requeued = [c.args[0] for c in mock_delay.call_args_list]
        self.assertIn(str(live.id), requeued)
        self.assertNotIn(str(spent.id), requeued)

    @patch("apps.notifications.tasks.send_notification_task.delay")
    def test_sweep_abandons_rows_past_the_give_up_window(self, mock_delay):
        """The outage path never increments attempt_count, so without an age
        ceiling this row would be re-queued hourly forever."""
        from datetime import timedelta
        from django.utils import timezone
        from apps.notifications.models import Notification, NotificationStatus
        from apps.notifications.tasks import GIVE_UP_AFTER_DAYS, retry_failed_notifications_task

        parked = self._notification(attempt_count=0)
        # auto_now_add means created_at must be forced past the window
        Notification.objects.filter(pk=parked.pk).update(
            created_at=timezone.now() - timedelta(days=GIVE_UP_AFTER_DAYS + 1)
        )

        retry_failed_notifications_task()

        parked.refresh_from_db()
        self.assertEqual(parked.status, NotificationStatus.ABANDONED)
        self.assertEqual(mock_delay.call_count, 0)

    @patch("apps.notifications.tasks.send_notification_task.delay")
    def test_abandoned_rows_are_never_swept_again(self, mock_delay):
        from apps.notifications.models import NotificationStatus
        from apps.notifications.tasks import retry_failed_notifications_task
        self._notification(status=NotificationStatus.ABANDONED, attempt_count=0)

        retry_failed_notifications_task()

        self.assertEqual(mock_delay.call_count, 0)

    @patch("apps.notifications.services.send_now", return_value=False)
    def test_send_task_abandons_once_the_budget_is_spent(self, mock_send_now):
        from apps.notifications.models import NotificationStatus
        from apps.notifications.tasks import MAX_ATTEMPTS, send_notification_task

        n = self._notification(attempt_count=MAX_ATTEMPTS)
        # Simulate Celery on its final retry: request.retries is 0-indexed, so
        # MAX_ATTEMPTS - 1 means "this is attempt MAX_ATTEMPTS".
        send_notification_task.push_request(retries=MAX_ATTEMPTS - 1)
        try:
            send_notification_task.run(str(n.id), force=True)
        finally:
            send_notification_task.pop_request()

        n.refresh_from_db()
        self.assertEqual(n.status, NotificationStatus.ABANDONED)

    def test_retry_delay_is_flat_not_exponential(self):
        from apps.notifications import tasks
        self.assertEqual(tasks.RETRY_DELAY_SECONDS, 300)
        self.assertEqual(tasks.MAX_ATTEMPTS, 3)
