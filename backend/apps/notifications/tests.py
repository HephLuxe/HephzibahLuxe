"""
apps/notifications/tests.py

Run with: python manage.py test notifications
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (
    Notification,
    NotificationStatus,
    ServiceHealthState,
    ServiceStatus,
)
from .services import (
    BREVO_SERVICE,
    REDACTED_CONTEXT,
    queue_notification,
    send_now,
)
from .tasks import send_notification_task

User = get_user_model()


class NotificationServiceTests(TestCase):
    @patch("apps.notifications.services._send_via_brevo")
    def test_queue_notification_creates_row_and_dispatches_task(self, mock_send):
        # Background dispatch is inline under the test runner (BACKGROUND_EAGER,
        # and this process never calls background.enable_async()), so .delay()
        # actually runs send_notification_task -> send_now() here — the Brevo
        # call itself must be mocked to avoid a real network call.
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


def _set_brevo_down(minutes_ago=0):
    """Force the Brevo breaker open.

    update_or_create, not filter().update(): the "brevo" row is seeded by
    migration 0012, and a queryset update against a missing row silently matches
    nothing — leaving the breaker closed and the test asserting the opposite of
    what it reads. That is not hypothetical; a stale reused test database
    (pytest --reuse-db) produced exactly that, and the failure pointed at the
    breaker rather than at the fixture.

    last_failure_at is always set, because is_down() ignores a `down` verdict
    older than DOWN_STALE_AFTER and one with no recorded failure at all.
    """
    from datetime import timedelta

    ServiceHealthState.objects.update_or_create(
        service=BREVO_SERVICE,
        defaults={
            "status": ServiceStatus.DOWN,
            "consecutive_failures": 3,
            "last_failure_at": timezone.now() - timedelta(minutes=minutes_ago),
        },
    )


def _make_notification():
    return Notification.objects.create(
        recipient_email="notif@example.com",
        template_name="payment_due",
        subject="Payment due",
        context={"label": "Deposit"},
    )


class BrevoOutageDetectionTests(TestCase):
    """Detection via the real send path — one deduped escalation per outage on
    the up->down transition, recovery on the down->up transition, and no
    retry-budget burn while Brevo is known-down. This is the only detection path
    now: the active 5-minute probe was removed with Celery."""

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
        # so the retry sweep keeps re-trying it instead of stranding it.
        n = _make_notification()
        send_now(n)
        n.refresh_from_db()
        self.assertEqual(n.status, NotificationStatus.FAILED)
        self.assertEqual(n.attempt_count, 0)

    @patch("apps.notifications.services._drain_after_recovery")
    @patch("apps.notifications.services._emit_brevo_recovered")
    @patch("apps.notifications.services._send_via_brevo")
    def test_success_after_outage_recovers_and_drains(self, mock_send, mock_recovered, mock_drain):
        _set_brevo_down()
        # A clean send (mock does not raise) should flip up and drain the backlog.
        send_now(_make_notification())

        state = ServiceHealthState.objects.get(service=BREVO_SERVICE)
        self.assertEqual(state.status, ServiceStatus.UP)
        mock_recovered.assert_called_once()
        mock_drain.assert_called_once()


class BrevoBreakerTests(TestCase):
    @patch("apps.notifications.services.send_now")
    def test_send_task_defers_without_attempting_while_down(self, mock_send_now):
        _set_brevo_down()
        n = _make_notification()

        send_notification_task.delay(str(n.id))  # force defaults False

        mock_send_now.assert_not_called()  # breaker parked it, no Brevo hit
        n.refresh_from_db()
        # DEFERRED, not FAILED: nothing was attempted. The row used to be labelled
        # FAILED with attempt_count=0, which told staff a send had failed when none
        # had happened.
        self.assertEqual(n.status, NotificationStatus.DEFERRED)
        self.assertIn("Deferred", n.error_message)
        self.assertEqual(n.attempt_count, 0)


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
        """One attempt per dispatch: the task reads the attempt_count send_now
        left behind and gives up when the budget is gone. There is no
        `self.retry` to simulate any more — the sweep is the only retry path."""
        from apps.notifications.models import NotificationStatus
        from apps.notifications.tasks import MAX_ATTEMPTS, send_notification_task

        n = self._notification(attempt_count=MAX_ATTEMPTS)
        send_notification_task.run(str(n.id), force=True)

        n.refresh_from_db()
        self.assertEqual(n.status, NotificationStatus.ABANDONED)

    @patch("apps.notifications.services.send_now", return_value=False)
    def test_send_task_leaves_a_row_with_budget_left_for_the_sweep(self, mock_send_now):
        from apps.notifications.models import NotificationStatus
        from apps.notifications.tasks import MAX_ATTEMPTS, send_notification_task

        n = self._notification(attempt_count=MAX_ATTEMPTS - 1)
        send_notification_task.run(str(n.id), force=True)

        n.refresh_from_db()
        self.assertEqual(n.status, NotificationStatus.FAILED)

    def test_there_is_no_in_task_retry_delay_left(self):
        """Guard against reintroducing in-task retry. send_now increments
        attempt_count per call, so a loop inside one dispatch would spend the
        whole budget and the sweep (attempt_count__lt=MAX) would skip the row
        for ever."""
        from apps.notifications import tasks
        self.assertEqual(tasks.MAX_ATTEMPTS, 3)
        self.assertFalse(hasattr(tasks, "RETRY_DELAY_SECONDS"))


class StrandedQueuedSweepTests(TestCase):
    """
    The hole the broker used to plug. A row is created QUEUED and committed, then
    the process dies before its in-memory dispatch runs — with no broker to
    redeliver, only this sweep will ever look at it again.
    """

    def _queued(self, age_minutes):
        from datetime import timedelta

        from apps.notifications.models import Notification, NotificationStatus

        n = Notification.objects.create(
            recipient_email="stranded@example.com",
            template_name="new_reminder",
            subject="New reminder",
            status=NotificationStatus.QUEUED,
        )
        Notification.objects.filter(pk=n.pk).update(
            created_at=timezone.now() - timedelta(minutes=age_minutes)
        )
        return n

    @patch("apps.notifications.tasks.send_notification_task.delay")
    def test_a_stranded_queued_row_is_re_driven(self, mock_delay):
        from apps.notifications.tasks import retry_failed_notifications_task
        stranded = self._queued(age_minutes=30)

        retry_failed_notifications_task()

        requeued = [c.args[0] for c in mock_delay.call_args_list]
        self.assertIn(str(stranded.id), requeued)

    @patch("apps.notifications.tasks.send_notification_task.delay")
    def test_a_fresh_queued_row_is_left_alone(self, mock_delay):
        """Without the age floor the sweep would race the pool and re-drive a
        send that is in flight right now, double-mailing the recipient."""
        from apps.notifications.tasks import retry_failed_notifications_task
        fresh = self._queued(age_minutes=1)

        retry_failed_notifications_task()

        requeued = [c.args[0] for c in mock_delay.call_args_list]
        self.assertNotIn(str(fresh.id), requeued)

    @patch("apps.notifications.tasks.send_notification_task.delay")
    def test_auth_mail_is_swept_before_anything_else(self, mock_delay):
        """The sweep runs inline in a cron process with a finite lifetime. If it
        is killed part-way through, the thing that survives being dropped should
        be the digest, not the reset code that expires in 30 minutes."""
        from apps.notifications.models import Notification, NotificationStatus
        from apps.notifications.tasks import retry_failed_notifications_task

        digest = Notification.objects.create(
            recipient_email="a@example.com", template_name="payment_due",
            subject="Payment due", status=NotificationStatus.FAILED,
        )
        reset = Notification.objects.create(
            recipient_email="b@example.com", template_name="password_reset",
            subject="Password reset", status=NotificationStatus.FAILED,
        )

        retry_failed_notifications_task()

        order = [c.args[0] for c in mock_delay.call_args_list]
        self.assertEqual(order.index(str(reset.id)) < order.index(str(digest.id)), True)


class AuthSecretScrubTests(TestCase):
    """
    P0-1: `context` is the exact params dict sent to Brevo, so for these two
    templates it holds a live credential. It must not survive the send.
    """

    def _auth_notification(self, template_name, **kw):
        from apps.notifications.models import Notification
        defaults = dict(
            recipient_email="secret@example.com",
            template_name=template_name,
            subject="x",
            context={"code": "418302", "temporary_password": "Xk9m2QpLr4vT"},
        )
        defaults.update(kw)
        return Notification.objects.create(**defaults)

    @patch("apps.notifications.services._send_via_brevo")
    def test_context_is_redacted_once_sent(self, mock_send):
        n = self._auth_notification("password_reset")
        send_now(n)
        n.refresh_from_db()
        self.assertEqual(n.context, REDACTED_CONTEXT)

    @patch("apps.notifications.services._send_via_brevo")
    def test_a_non_auth_template_keeps_its_context(self, mock_send):
        """Support needs to see what was actually mailed for everything else."""
        n = self._auth_notification("payment_due", context={"label": "Deposit"})
        send_now(n)
        n.refresh_from_db()
        self.assertEqual(n.context, {"label": "Deposit"})

    @patch("apps.notifications.services._send_via_brevo")
    def test_the_secret_still_reaches_brevo(self, mock_send):
        """The scrub must happen after the send, not instead of it."""
        n = self._auth_notification("password_reset")
        send_now(n)
        _, kwargs = mock_send.call_args
        self.assertEqual(kwargs["params"]["code"], "418302")

    @patch("apps.notifications.services.send_now", return_value=False)
    def test_context_is_redacted_when_abandoned(self, mock_send_now):
        from apps.notifications.models import NotificationStatus
        from apps.notifications.tasks import MAX_ATTEMPTS, send_notification_task

        n = self._auth_notification("user_credentials", attempt_count=MAX_ATTEMPTS,
                                    status=NotificationStatus.FAILED)
        send_notification_task.run(str(n.id), force=True)

        n.refresh_from_db()
        self.assertEqual(n.status, NotificationStatus.ABANDONED)
        self.assertEqual(n.context, REDACTED_CONTEXT)

    @patch("apps.notifications.tasks.send_notification_task.delay")
    def test_context_is_redacted_when_the_sweep_gives_up(self, mock_delay):
        from datetime import timedelta

        from apps.notifications.models import Notification, NotificationStatus
        from apps.notifications.tasks import GIVE_UP_AFTER_DAYS, retry_failed_notifications_task

        n = self._auth_notification("password_reset", status=NotificationStatus.FAILED)
        Notification.objects.filter(pk=n.pk).update(
            created_at=timezone.now() - timedelta(days=GIVE_UP_AFTER_DAYS + 1)
        )

        retry_failed_notifications_task()

        n.refresh_from_db()
        self.assertEqual(n.status, NotificationStatus.ABANDONED)
        self.assertEqual(n.context, REDACTED_CONTEXT)

    @patch("apps.notifications.services.send_now", return_value=False)
    def test_a_failed_row_keeps_its_secret_so_the_sweep_can_resend(self, mock_send_now):
        """FAILED is not terminal — the sweep re-reads `context` to re-send. Scrub
        it here and the client gets a credentials email with no credential."""
        from apps.notifications.models import NotificationStatus
        from apps.notifications.tasks import send_notification_task

        n = self._auth_notification("password_reset", attempt_count=0,
                                    status=NotificationStatus.FAILED)
        send_notification_task.run(str(n.id), force=True)

        n.refresh_from_db()
        self.assertEqual(n.context["code"], "418302")


class BrevoBreakerStalenessTests(TestCase):
    """
    A `down` verdict is only cleared by a successful send, and while it is set
    every normal send parks itself without attempting. Without a ceiling, a stale
    row mutes the entire platform indefinitely — and there is no active probe
    left to break the tie.
    """

    @staticmethod
    def _stale_minutes():
        return int(ServiceHealthState.DOWN_STALE_AFTER.total_seconds() // 60) + 1

    def test_a_fresh_down_verdict_still_blocks(self):
        _set_brevo_down(minutes_ago=1)
        self.assertTrue(ServiceHealthState.is_down(BREVO_SERVICE))

    def test_a_stale_down_verdict_stops_blocking(self):
        _set_brevo_down(minutes_ago=self._stale_minutes())
        self.assertFalse(ServiceHealthState.is_down(BREVO_SERVICE))

    def test_a_stale_verdict_is_not_silently_rewritten_to_up(self):
        """The admin must still show the last real verdict — only the breaker
        relaxes, and the next real send outcome is what updates the row."""
        _set_brevo_down(minutes_ago=self._stale_minutes())
        ServiceHealthState.is_down(BREVO_SERVICE)
        self.assertEqual(
            ServiceHealthState.objects.get(service=BREVO_SERVICE).status, ServiceStatus.DOWN
        )

    def test_down_with_no_recorded_failure_never_parks_mail(self):
        ServiceHealthState.objects.update_or_create(
            service=BREVO_SERVICE,
            defaults={"status": ServiceStatus.DOWN, "last_failure_at": None},
        )
        self.assertFalse(ServiceHealthState.is_down(BREVO_SERVICE))


class DeferredStatusTests(TestCase):
    """
    P2-4. A row the breaker parked was never attempted, and calling it FAILED with
    attempt_count=0 told staff a send had been tried and had failed. DEFERRED is
    purely about what the admin says — the sweep treats it identically.
    """

    def test_the_breaker_parks_as_deferred_not_failed(self):
        _set_brevo_down()
        n = _make_notification()

        send_notification_task.run(str(n.id))  # force=False, the normal path

        n.refresh_from_db()
        self.assertEqual(n.status, NotificationStatus.DEFERRED)
        self.assertEqual(n.attempt_count, 0)

    def test_a_genuine_send_failure_is_still_failed(self):
        """The distinction has to cut both ways or it is just a rename."""
        with patch("apps.notifications.services._send_via_brevo") as mock_send:
            mock_send.side_effect = RuntimeError("Brevo send error 400: bad request")
            n = _make_notification()
            send_notification_task.run(str(n.id), force=True)

        n.refresh_from_db()
        self.assertEqual(n.status, NotificationStatus.FAILED)
        self.assertEqual(n.attempt_count, 1)

    @patch("apps.notifications.tasks.send_notification_task.delay")
    def test_the_sweep_re_drives_deferred_exactly_like_failed(self, mock_delay):
        from apps.notifications.tasks import retry_failed_notifications_task

        deferred = Notification.objects.create(
            recipient_email="deferred@example.com", template_name="new_reminder",
            subject="New reminder", status=NotificationStatus.DEFERRED,
        )

        retry_failed_notifications_task()

        self.assertIn(str(deferred.id), [c.args[0] for c in mock_delay.call_args_list])

    @patch("apps.notifications.tasks.send_notification_task.delay")
    def test_a_deferred_row_past_the_give_up_window_is_abandoned(self, mock_delay):
        """The outage path spends no attempt, so the age ceiling is the only stop
        a DEFERRED row has."""
        from datetime import timedelta

        from apps.notifications.tasks import GIVE_UP_AFTER_DAYS, retry_failed_notifications_task

        parked = Notification.objects.create(
            recipient_email="old-deferred@example.com", template_name="new_reminder",
            subject="New reminder", status=NotificationStatus.DEFERRED,
        )
        Notification.objects.filter(pk=parked.pk).update(
            created_at=timezone.now() - timedelta(days=GIVE_UP_AFTER_DAYS + 1)
        )

        retry_failed_notifications_task()

        parked.refresh_from_db()
        self.assertEqual(parked.status, NotificationStatus.ABANDONED)

    def test_the_weekly_cleanup_never_deletes_a_deferred_row(self):
        """DEFERRED is not terminal — the sweep still owns it."""
        from datetime import timedelta

        from apps.notifications.tasks import cleanup_old_notifications_task

        n = Notification.objects.create(
            recipient_email="keep-deferred@example.com", template_name="new_reminder",
            subject="New reminder", status=NotificationStatus.DEFERRED,
        )
        Notification.objects.filter(pk=n.pk).update(
            created_at=timezone.now() - timedelta(days=200)
        )

        cleanup_old_notifications_task()

        self.assertTrue(Notification.objects.filter(pk=n.pk).exists())

    def test_deferred_is_a_selectable_status_on_the_history_api(self):
        """The staff status filter validates against NotificationStatus.values, so
        a new status must be usable there without a code change."""
        self.assertIn("deferred", NotificationStatus.values)


@override_settings(TESTING=False)
class BrevoSenderIdentityTests(TestCase):
    """
    Who the platform mails as is a deploy-time setting, not dashboard state.

    TESTING=False is required: _send_via_brevo returns immediately under the
    test runner, so the guard has to be lifted to reach the payload at all. The
    network is stubbed by patching _brevo_api, so nothing leaves the process.
    """

    def payload(self, template_name=None):
        """Build one real send and return the SendSmtpEmail handed to Brevo."""
        from apps.notifications.services import _send_via_brevo

        with patch("apps.notifications.services._brevo_api") as api:
            _send_via_brevo(
                "client@example.com", 42, {"first_name": "Ada"},
                template_name=template_name,
            )

        return api.return_value.send_transac_email.call_args.args[0]

    @override_settings(BREVO_SENDER_EMAIL="", BREVO_SENDER_NAME="", BREVO_REPLY_TO_EMAIL="")
    def test_blank_settings_defer_to_the_template(self):
        # The pre-existing behaviour: no sender field on the request at all, so
        # Brevo uses whatever the template carries. Omission — not an explicit
        # null — is what makes that fallback happen.
        email = self.payload()

        self.assertIsNone(email.sender)
        self.assertIsNone(email.reply_to)

    @override_settings(
        BREVO_SENDER_EMAIL="clientsupport@example.com",
        BREVO_SENDER_NAME="Client Support",
        BREVO_REPLY_TO_EMAIL="tosin@example.com",
    )
    def test_sender_and_reply_to_override_the_template(self):
        email = self.payload()

        self.assertEqual(email.sender, {"email": "clientsupport@example.com", "name": "Client Support"})
        self.assertEqual(email.reply_to, {"email": "tosin@example.com"})

    @override_settings(BREVO_SENDER_EMAIL="clientsupport@example.com", BREVO_SENDER_NAME="")
    def test_sender_without_a_name_sends_only_the_address(self):
        # A blank name must not become `"name": ""`, which Brevo would render as
        # an empty From name instead of falling back to the address.
        email = self.payload()

        self.assertEqual(email.sender, {"email": "clientsupport@example.com"})

    @override_settings(BREVO_SENDER_EMAIL="", BREVO_REPLY_TO_EMAIL="tosin@example.com")
    def test_reply_to_alone_leaves_the_template_sender_intact(self):
        # The two settings are independent: routing replies somewhere else must
        # not silently take over the From address as well.
        email = self.payload()

        self.assertIsNone(email.sender)
        self.assertEqual(email.reply_to, {"email": "tosin@example.com"})

    @override_settings(BREVO_SENDER_EMAIL="clientsupport@example.com")
    def test_the_rest_of_the_payload_is_untouched(self):
        email = self.payload()

        self.assertEqual(email.to, [{"email": "client@example.com"}])
        self.assertEqual(email.template_id, 42)
        self.assertEqual(email.params, {"first_name": "Ada"})


@override_settings(TESTING=False, **{'BREVO_SENDER_EMAIL': 'clientsupport@example.com', 'BREVO_SENDER_NAME': 'Client Support', 'BREVO_REPLY_TO_EMAIL': 'tosin@example.com'})
class BrevoPerTemplateSenderTests(TestCase):
    """
    One template can carry its own identity without reintroducing per-template
    dashboard state. The platform default above applies to everything else.
    """

    INTERNAL = "inquiry_submitted_internal"

    def payload(self, template_name):
        from apps.notifications.services import _send_via_brevo

        with patch("apps.notifications.services._brevo_api") as api:
            _send_via_brevo(
                "staff@example.com", 42, {}, template_name=template_name,
            )

        return api.return_value.send_transac_email.call_args.args[0]

    @override_settings(BREVO_SENDER_OVERRIDES={
        INTERNAL: {"email": "alerts@example.com", "name": "Lead Alerts", "reply_to": ""},
    })
    def test_an_override_replaces_the_platform_identity(self):
        email = self.payload(self.INTERNAL)

        self.assertEqual(email.sender, {"email": "alerts@example.com", "name": "Lead Alerts"})

    @override_settings(BREVO_SENDER_OVERRIDES={
        INTERNAL: {"email": "alerts@example.com", "name": "Lead Alerts", "reply_to": ""},
    })
    def test_other_templates_keep_the_platform_identity(self):
        # The override is scoped to its own template and nothing else.
        email = self.payload("password_reset")

        self.assertEqual(email.sender, {"email": "clientsupport@example.com", "name": "Client Support"})

    @override_settings(BREVO_SENDER_OVERRIDES={
        INTERNAL: {"email": "alerts@example.com", "name": "", "reply_to": ""},
    })
    def test_an_override_does_not_inherit_the_platform_name(self):
        # The whole point of resolving email+name together: inheriting the name
        # field-by-field would send "Client Support <alerts@example.com>".
        email = self.payload(self.INTERNAL)

        self.assertEqual(email.sender, {"email": "alerts@example.com"})

    @override_settings(BREVO_SENDER_OVERRIDES={
        INTERNAL: {"email": "alerts@example.com", "name": "Lead Alerts", "reply_to": ""},
    })
    def test_reply_to_still_falls_back_independently(self):
        # Overriding WHO it is from does not change where replies go.
        email = self.payload(self.INTERNAL)

        self.assertEqual(email.reply_to, {"email": "tosin@example.com"})

    @override_settings(BREVO_SENDER_OVERRIDES={
        INTERNAL: {"email": "", "name": "", "reply_to": "leads@example.com"},
    })
    def test_a_reply_to_only_override_leaves_the_sender_alone(self):
        email = self.payload(self.INTERNAL)

        self.assertEqual(email.sender, {"email": "clientsupport@example.com", "name": "Client Support"})
        self.assertEqual(email.reply_to, {"email": "leads@example.com"})

    @override_settings(BREVO_SENDER_OVERRIDES={})
    def test_no_override_declared_is_the_platform_identity(self):
        email = self.payload(self.INTERNAL)

        self.assertEqual(email.sender, {"email": "clientsupport@example.com", "name": "Client Support"})

    def test_send_now_passes_the_template_name_through(self):
        """The wiring that makes any of this reachable in production."""
        from apps.notifications.services import send_now

        user = User.objects.create_user(
            first_name="A", last_name="B", email="staff2@example.com", password="x", role="staff",
        )
        notification = Notification.objects.create(
            recipient_email=user.email,
            template_name=self.INTERNAL,
            subject="x",
            context={},
        )

        with patch("apps.notifications.services._send_via_brevo") as send:
            send_now(notification)

        self.assertEqual(send.call_args.kwargs["template_name"], self.INTERNAL)


class BrevoClientCachingTests(TestCase):
    """
    P2-8. The SDK client used to be rebuilt per send — a fresh Configuration,
    ApiClient and urllib3 PoolManager, so every email paid for a new TLS
    handshake and the connection pool was never reused.
    """

    def tearDown(self):
        from apps.notifications.services import reset_brevo_client
        reset_brevo_client()

    def test_the_client_is_built_once_and_reused(self):
        from apps.notifications.services import _brevo_api, reset_brevo_client

        reset_brevo_client()
        self.assertIs(_brevo_api(), _brevo_api())

    def test_the_connection_pool_is_sized_from_the_background_pool(self):
        """urllib3 discards connections past maxsize with a warning. The SDK's own
        default is cpu_count()*5 — a number about the machine, and inside a
        container about the HOST's machine — so it is pinned to the number of
        senders we actually run."""
        from django.conf import settings

        from apps.notifications.services import _brevo_api, reset_brevo_client

        reset_brevo_client()
        maxsize = _brevo_api().api_client.configuration.connection_pool_maxsize

        self.assertGreaterEqual(maxsize, settings.BACKGROUND_MAX_WORKERS)

    def test_reset_forces_a_rebuild(self):
        from apps.notifications.services import _brevo_api, reset_brevo_client

        first = _brevo_api()
        reset_brevo_client()
        self.assertIsNot(_brevo_api(), first)
