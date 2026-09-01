"""
apps/document_hub/tests.py

Run with: python manage.py test document_hub
"""

import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.events.models import Event
from apps.portal.models import ClientPortal, EventEngagement

from . import views
from .models import ClientDocumentCategory, PaymentSchedule

User = get_user_model()


def _dummy_file(name: str = "doc.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 fake pdf content", content_type="application/pdf")


class DocumentHubTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.client_user = User.objects.create_user(
            first_name="Test", last_name="Client", email="hub-client@example.com", password="x"
        )
        self.staff = User.objects.create_user(
            first_name="Test", last_name="Staff", email="hub-staff@example.com", password="x", role="staff"
        )
        self.portal = ClientPortal.objects.get(user=self.client_user)
        self.event = Event.objects.create(
            celebrant=self.client_user, title="Priscilla & Samuel's Wedding",
            event_type="Wedding", bride_name="Priscilla Adeyemi", groom_name="Samuel Okonkwo",
            country="NG", state="Lagos", event_date=datetime.date(2026, 11, 27),
        )
        self.engagement = EventEngagement.objects.create(
            portal=self.portal, event=self.event, is_active=True, current_phase="connect"
        )

    def test_empty_hub_returns_nulls_and_empty_lists(self):
        req = self.factory.get("/api/v1/document-hub/")
        force_authenticate(req, user=self.client_user)
        r = views.get_hub(req)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["service_agreements"], [])
        self.assertEqual(r.data["quotations"], [])
        self.assertEqual(r.data["welcome_service_info"], [])
        self.assertIsNone(r.data["payment_schedule"])
        self.assertEqual(r.data["invoices"], [])
        self.assertEqual(r.data["receipts"], [])

    def test_staff_creates_contract_gets_auto_reference_code(self):
        req = self.factory.post(
            "/api/v1/document-hub/documents/",
            {
                "portal_id": str(self.portal.id),
                "category": ClientDocumentCategory.SVC_AGREEMENT,
                "title": "Event Management Contract",
                "is_signed": True,
                "signed_on": "2026-05-13",
                "file": _dummy_file("contract.pdf"),
            },
            format="multipart",
        )
        force_authenticate(req, user=self.staff)
        r = views.create_document(req)
        self.assertEqual(r.status_code, 201)

        req = self.factory.get("/api/v1/document-hub/")
        force_authenticate(req, user=self.client_user)
        r = views.get_hub(req)
        # System-generated: HL-<II><CODE><NNN>-C001. This engagement is a Wedding
        # for Priscilla & Samuel, so the segment is PSW### (P+S+W).
        self.assertRegex(r.data["service_agreements"][0]["reference_code"], r"^HL-PSW\d{3}-C001$")
        self.assertEqual(r.data["quotations"], [])

    def test_every_quotation_is_returned_not_just_the_latest(self):
        # The regression this guards: the hub used to expose `quotation` as a
        # single `.first()`, so uploading a revised quotation hid the original
        # from the client entirely — while `next_reference_code` had already
        # numbered it Q002, proving Q001 was still there. A revised quotation is
        # the normal case, not an edge one.
        for n in range(2):
            req = self.factory.post(
                "/api/v1/document-hub/documents/",
                {
                    "portal_id": str(self.portal.id),
                    "category": ClientDocumentCategory.QUOTATION,
                    "title": f"Service Quotation {n + 1}",
                    "file": _dummy_file(f"quote{n + 1}.pdf"),
                },
                format="multipart",
            )
            force_authenticate(req, user=self.staff)
            self.assertEqual(views.create_document(req).status_code, 201)

        req = self.factory.get("/api/v1/document-hub/")
        force_authenticate(req, user=self.client_user)
        r = views.get_hub(req)

        codes = [q["reference_code"] for q in r.data["quotations"]]
        self.assertEqual(len(codes), 2)
        # Meta.ordering is ["order", "-created_at"] and both rows default to
        # order=0, so the newest revision leads.
        self.assertRegex(codes[0], r"^HL-PSW\d{3}-Q002$")
        self.assertRegex(codes[1], r"^HL-PSW\d{3}-Q001$")

    def test_client_cannot_create_document(self):
        req = self.factory.post(
            "/api/v1/document-hub/documents/",
            {"portal_id": str(self.portal.id), "category": ClientDocumentCategory.FAQ, "title": "FAQ"},
            format="multipart",
        )
        force_authenticate(req, user=self.client_user)
        self.assertEqual(views.create_document(req).status_code, 403)

    def test_client_supplied_reference_code_is_ignored(self):
        # reference_code is read-only/system-generated — a client-sent value is
        # dropped and the system code is used instead (no 400).
        req = self.factory.post(
            "/api/v1/document-hub/documents/",
            {
                "portal_id": str(self.portal.id),
                "category": ClientDocumentCategory.SVC_AGREEMENT,
                "reference_code": "not-a-valid-code",
                "title": "Contract",
                "file": _dummy_file(),
            },
            format="multipart",
        )
        force_authenticate(req, user=self.staff)
        r = views.create_document(req)
        self.assertEqual(r.status_code, 201)
        self.assertRegex(r.data["reference_code"], r"^HL-PSW\d{3}-C001$")

    def _schedule(self, total="7000000"):
        """Create a schedule through the API and return (id, its invoices by label)."""
        req = self.factory.post(
            "/api/v1/document-hub/payment-schedule/",
            {"portal_id": str(self.portal.id), "total_investment": total},
            format="json",
        )
        force_authenticate(req, user=self.staff)
        r = views.create_payment_schedule(req)
        self.assertEqual(r.status_code, 201)
        schedule = PaymentSchedule.objects.get(id=r.data["id"])
        return schedule, {inv.milestone.label: inv for inv in schedule.engagement.invoices.all()}

    def _pay(self, invoice, amount=None):
        """Flip an invoice to paid through the API — the path staff actually use."""
        body = {"status": "paid"}
        if amount is not None:
            body["amount"] = amount
        req = self.factory.patch(f"/api/v1/document-hub/invoices/{invoice.id}/", body, format="json")
        force_authenticate(req, user=self.staff)
        r = views.invoice_detail(req, invoice_id=str(invoice.id))
        self.assertEqual(r.status_code, 200)
        return r

    def _hub_schedule(self):
        req = self.factory.get("/api/v1/document-hub/")
        force_authenticate(req, user=self.client_user)
        return views.get_hub(req).data["payment_schedule"]

    def test_creating_a_schedule_issues_one_linked_invoice_per_milestone(self):
        # The double entry this removes: staff used to set a total, get three
        # milestones, and then hand-create three invoices mirroring them.
        schedule, invoices = self._schedule()

        self.assertEqual(schedule.milestones.count(), 3)
        self.assertEqual(sorted(invoices), ["Deposit", "Final Payment", "Phase 2"])
        self.assertEqual(invoices["Deposit"].amount, Decimal("2100000.00"))
        self.assertEqual(invoices["Phase 2"].amount, Decimal("2800000.00"))
        # No agreed due date on the milestone yet, so none is invented — an
        # invented one would show the client an already-overdue invoice.
        self.assertIsNone(invoices["Deposit"].due_on)

    def test_paying_an_invoice_moves_the_payment_schedule(self):
        # THE regression. Flipping an invoice to paid used to write one column
        # that nothing downstream read: paid_to_date stayed 0.00 and staff had
        # to mark the milestone paid as a second, unlinked action.
        schedule, invoices = self._schedule()

        self._pay(invoices["Deposit"])

        milestone = schedule.milestones.get(label="Deposit")
        self.assertEqual(milestone.status, "paid")
        self.assertEqual(milestone.amount_paid, Decimal("2100000.00"))
        self.assertIsNotNone(milestone.paid_on)

        tiles = self._hub_schedule()
        self.assertEqual(tiles["paid_to_date"], "2100000.00")
        self.assertEqual(tiles["remaining_balance"], "4900000.00")

    def test_a_part_payment_is_recorded_as_part_paid(self):
        # Real money rarely matches the plan: a 1,500,000 payment against a
        # 2,800,000 phase is neither paid nor pending, and rounding it to either
        # one makes the tiles lie.
        schedule, invoices = self._schedule()

        self._pay(invoices["Phase 2"], amount="1500000")

        milestone = schedule.milestones.get(label="Phase 2")
        self.assertEqual(milestone.status, "part_paid")
        self.assertEqual(milestone.amount_paid, Decimal("1500000.00"))
        self.assertEqual(milestone.balance, Decimal("1300000.00"))

    def test_next_payment_due_is_the_outstanding_balance(self):
        schedule, invoices = self._schedule()
        self._pay(invoices["Deposit"])
        # Part-pay the next one: it is still what's owed next, for its balance
        # rather than its face value.
        self._pay(invoices["Phase 2"], amount="1000000")

        tiles = self._hub_schedule()
        self.assertEqual(tiles["paid_to_date"], "3100000.00")
        self.assertEqual(tiles["next_payment_due_amount"], Decimal("1800000.00"))

    def test_unpaying_an_invoice_takes_the_money_back_off(self):
        # The sync recomputes from the full invoice set rather than adding a
        # delta, so every reversal converges instead of drifting.
        schedule, invoices = self._schedule()
        self._pay(invoices["Deposit"])

        req = self.factory.patch(
            f"/api/v1/document-hub/invoices/{invoices['Deposit'].id}/",
            {"status": "pending"}, format="json",
        )
        force_authenticate(req, user=self.staff)
        views.invoice_detail(req, invoice_id=str(invoices["Deposit"].id))

        milestone = schedule.milestones.get(label="Deposit")
        self.assertEqual(milestone.status, "pending")
        self.assertEqual(milestone.amount_paid, Decimal("0"))
        self.assertIsNone(milestone.paid_on)

    def test_deleting_a_paid_invoice_reverses_its_milestone(self):
        schedule, invoices = self._schedule()
        self._pay(invoices["Deposit"])

        req = self.factory.delete(f"/api/v1/document-hub/invoices/{invoices['Deposit'].id}/")
        force_authenticate(req, user=self.staff)
        self.assertEqual(
            views.invoice_detail(req, invoice_id=str(invoices["Deposit"].id)).status_code, 200
        )

        milestone = schedule.milestones.get(label="Deposit")
        self.assertEqual(milestone.amount_paid, Decimal("0"))
        self.assertEqual(self._hub_schedule()["paid_to_date"], "0.00")

    def test_milestone_status_cannot_be_written_directly(self):
        # Derived from the invoices, so accepting it here would let a PATCH set
        # a figure the next invoice edit silently overwrites.
        schedule, _ = self._schedule()
        milestone = schedule.milestones.get(label="Deposit")

        req = self.factory.patch(
            f"/api/v1/document-hub/milestones/{milestone.id}/",
            {"status": "paid", "amount_paid": "2100000"}, format="json",
        )
        force_authenticate(req, user=self.staff)
        r = views.milestone_detail(req, milestone_id=str(milestone.id))

        self.assertEqual(r.status_code, 200)
        milestone.refresh_from_db()
        self.assertEqual(milestone.status, "pending")
        self.assertEqual(milestone.amount_paid, Decimal("0"))

    def test_a_milestone_due_date_fills_its_invoice(self):
        schedule, invoices = self._schedule()
        milestone = schedule.milestones.get(label="Deposit")

        req = self.factory.patch(
            f"/api/v1/document-hub/milestones/{milestone.id}/",
            {"due_date": "2026-05-30"}, format="json",
        )
        force_authenticate(req, user=self.staff)
        views.milestone_detail(req, milestone_id=str(milestone.id))

        invoices["Deposit"].refresh_from_db()
        self.assertEqual(str(invoices["Deposit"].due_on), "2026-05-30")

    def test_mark_milestone_paid_flips_the_invoices_behind_it(self):
        # The endpoint still works, but it goes through the invoices rather than
        # writing the milestone — otherwise the invoices would still read
        # "pending" and the next invoice edit would recompute the milestone
        # straight back to unpaid.
        schedule, invoices = self._schedule()
        milestone = schedule.milestones.get(label="Deposit")

        req = self.factory.patch(f"/api/v1/document-hub/milestones/{milestone.id}/mark-paid/", {}, format="json")
        force_authenticate(req, user=self.staff)
        r = views.mark_milestone_paid(req, milestone_id=str(milestone.id))

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "paid")
        invoices["Deposit"].refresh_from_db()
        self.assertEqual(invoices["Deposit"].status, "paid")

    def test_mark_milestone_paid_still_works_without_an_invoice(self):
        # Ad-hoc and pre-link milestones have nothing else to record the payment
        # on, so they are still written directly.
        schedule = PaymentSchedule.objects.create(engagement=self.engagement, total_investment=1000)
        milestone = schedule.milestones.create(label="Ad-hoc", amount=Decimal("1000"))

        req = self.factory.patch(f"/api/v1/document-hub/milestones/{milestone.id}/mark-paid/", {}, format="json")
        force_authenticate(req, user=self.staff)
        r = views.mark_milestone_paid(req, milestone_id=str(milestone.id))

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "paid")
        self.assertIsNotNone(r.data["paid_on"])
        milestone.refresh_from_db()
        self.assertEqual(milestone.amount_paid, Decimal("1000"))

    def test_a_schedule_with_part_paid_milestones_cannot_be_deleted_unguarded(self):
        # Money that arrived is a payment record even when it did not settle the
        # milestone — the old PAID-only guard let those be deleted freely.
        schedule, invoices = self._schedule()
        self._pay(invoices["Deposit"], amount="500000")

        req = self.factory.delete(f"/api/v1/document-hub/payment-schedule/{schedule.id}/?confirm=true")
        force_authenticate(req, user=self.staff)
        r = views.update_payment_schedule(req, schedule_id=str(schedule.id))
        self.assertEqual(r.status_code, 400)


class PaymentDueDigestTests(TestCase):
    """
    The daily payment-due digest, and specifically its ordering. The marker
    (`reminder_sent_at`) must be committed BEFORE the email is queued: the old
    order — queue, then mark — left a window where the mail was already on its
    way and the marker was still NULL, so the next day's run billed the client
    by inbox a second time.
    """

    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Digest", last_name="Client",
            email="digest-client@example.com", password="x",
        )
        self.portal = ClientPortal.objects.get(user=self.client_user)
        self.event = Event.objects.create(
            celebrant=self.client_user, title="Digest Wedding", event_type="Wedding",
            bride_name="A", groom_name="B", country="NG", state="Lagos",
            event_date=datetime.date(2027, 3, 1),
        )
        self.engagement = EventEngagement.objects.create(
            portal=self.portal, event=self.event, is_active=True, current_phase="connect",
        )
        self.schedule = PaymentSchedule.objects.create(
            engagement=self.engagement, total_investment=Decimal("1000000"),
        )

    def _milestone(self, days_out=1):
        from .models import PaymentMilestone, PaymentMilestoneStatus
        return PaymentMilestone.objects.create(
            schedule=self.schedule, label="Deposit", amount=Decimal("500000"),
            due_date=datetime.date.today() + datetime.timedelta(days=days_out),
            status=PaymentMilestoneStatus.PENDING,
        )

    @mock.patch("apps.notifications.services._send_via_brevo")
    def test_a_due_milestone_is_emailed_and_marked(self, _mock_send):
        from apps.notifications.models import Notification

        from .tasks import payment_due_digest_task

        milestone = self._milestone()

        payment_due_digest_task()

        milestone.refresh_from_db()
        self.assertIsNotNone(milestone.reminder_sent_at)
        self.assertEqual(
            Notification.objects.filter(template_name="payment_due").count(), 1
        )

    @mock.patch("apps.notifications.services._send_via_brevo")
    def test_a_second_run_does_not_email_again(self, _mock_send):
        from apps.notifications.models import Notification

        from .tasks import payment_due_digest_task

        self._milestone()

        payment_due_digest_task()
        payment_due_digest_task()

        self.assertEqual(
            Notification.objects.filter(template_name="payment_due").count(), 1
        )

    def test_the_marker_is_already_committed_when_the_email_is_queued(self):
        """The actual ordering guarantee, asserted from inside the send path."""
        from .models import PaymentMilestone
        from .tasks import payment_due_digest_task

        milestone = self._milestone()
        observed = {}

        def spy(**kwargs):
            observed["marker"] = PaymentMilestone.objects.get(
                pk=milestone.pk
            ).reminder_sent_at
            return None

        # queue_notification is imported inside the task body, so patching it on
        # its source module is what the task actually resolves.
        with mock.patch("apps.notifications.services.queue_notification", side_effect=spy):
            payment_due_digest_task()

        self.assertIsNotNone(observed["marker"])

    @mock.patch("apps.notifications.services._send_via_brevo")
    def test_the_admin_kill_switch_stops_the_digest(self, _mock_send):
        from apps.notifications.models import Notification, ScheduledTaskSettings

        from .tasks import payment_due_digest_task

        ScheduledTaskSettings.objects.update_or_create(
            task_key="payment_due_digest",
            defaults={"label": "payment due", "is_enabled": False},
        )
        self._milestone()

        payment_due_digest_task()

        self.assertFalse(Notification.objects.filter(template_name="payment_due").exists())


class PaymentDueDigestTimezoneTests(TestCase):
    """
    P2-9, at the level that matters. The digest's lookahead has to be measured in
    the CLIENT's calendar, not UTC's — otherwise "due in 3 days" fires two or four
    days out for a client far enough from UTC.

    Every test here freezes `timezone.now()` at 23:30 UTC, which is the moment the
    two clients below are on different calendar days.
    """

    INSTANT = datetime.datetime(2026, 10, 10, 23, 30, tzinfo=datetime.timezone.utc)
    # At INSTANT: Auckland (UTC+13) is already the 11th; Midway (UTC-11) is the 10th.
    EAST_TZ = "Pacific/Auckland"
    WEST_TZ = "Pacific/Midway"

    def _client(self, email, tz):
        user = User.objects.create_user(
            first_name="TZ", last_name="Client", email=email, password="x",
        )
        user.timezone = tz
        user.save(update_fields=["timezone"])

        portal = ClientPortal.objects.get(user=user)
        event = Event.objects.create(
            celebrant=user, title=f"TZ Wedding {email}", event_type="Wedding",
            bride_name="A", groom_name="B", country="NG", state="Lagos",
            event_date=datetime.date(2027, 3, 1),
        )
        engagement = EventEngagement.objects.create(
            portal=portal, event=event, is_active=True, current_phase="connect",
        )
        schedule = PaymentSchedule.objects.create(
            engagement=engagement, total_investment=Decimal("1000000"),
        )
        return user, schedule

    def _milestone(self, schedule, due_date):
        from .models import PaymentMilestone, PaymentMilestoneStatus
        return PaymentMilestone.objects.create(
            schedule=schedule, label="Deposit", amount=Decimal("500000"),
            due_date=due_date, status=PaymentMilestoneStatus.PENDING,
        )

    def _run(self):
        from .tasks import payment_due_digest_task
        with mock.patch("django.utils.timezone.now", return_value=self.INSTANT):
            with mock.patch("apps.notifications.services._send_via_brevo"):
                payment_due_digest_task()

    def test_the_boundary_day_notifies_the_client_it_is_due_for(self):
        """A milestone on the 14th is exactly 3 days out for Auckland (local 11th)
        and 4 days out for Midway (local 10th). Under the old UTC-only maths both
        got the same answer, and for one of them it was wrong."""
        _east, east_schedule = self._client("east-due@example.com", self.EAST_TZ)
        _west, west_schedule = self._client("west-due@example.com", self.WEST_TZ)

        east_milestone = self._milestone(east_schedule, datetime.date(2026, 10, 14))
        west_milestone = self._milestone(west_schedule, datetime.date(2026, 10, 14))

        self._run()

        east_milestone.refresh_from_db()
        west_milestone.refresh_from_db()
        self.assertIsNotNone(east_milestone.reminder_sent_at)   # 3 days out locally
        self.assertIsNone(west_milestone.reminder_sent_at)      # still 4 days out

    def test_the_client_not_yet_due_is_picked_up_on_a_later_run(self):
        """Skipping is a deferral, not a drop."""
        _west, west_schedule = self._client("west-later@example.com", self.WEST_TZ)
        milestone = self._milestone(west_schedule, datetime.date(2026, 10, 14))

        self._run()
        milestone.refresh_from_db()
        self.assertIsNone(milestone.reminder_sent_at)

        # A day later, Midway's local date is the 11th and the 14th is 3 days out.
        later = self.INSTANT + datetime.timedelta(days=1)
        from .tasks import payment_due_digest_task
        with mock.patch("django.utils.timezone.now", return_value=later):
            with mock.patch("apps.notifications.services._send_via_brevo"):
                payment_due_digest_task()

        milestone.refresh_from_db()
        self.assertIsNotNone(milestone.reminder_sent_at)

    def test_an_overdue_milestone_is_still_notified(self):
        """There is deliberately no lower bound on due_date — an overdue payment
        stays in scope until it is paid or notified. The widened query must not
        have introduced one."""
        _east, east_schedule = self._client("east-overdue@example.com", self.EAST_TZ)
        milestone = self._milestone(east_schedule, datetime.date(2026, 9, 1))

        self._run()

        milestone.refresh_from_db()
        self.assertIsNotNone(milestone.reminder_sent_at)

    def test_a_client_with_no_timezone_uses_the_platform_default(self):
        _user, schedule = self._client("east-default@example.com", "")
        milestone = self._milestone(schedule, datetime.date(2026, 10, 14))

        from .tasks import payment_due_digest_task
        with override_settings(PLATFORM_DEFAULT_TIMEZONE=self.EAST_TZ):
            with mock.patch("django.utils.timezone.now", return_value=self.INSTANT):
                with mock.patch("apps.notifications.services._send_via_brevo"):
                    payment_due_digest_task()

        milestone.refresh_from_db()
        self.assertIsNotNone(milestone.reminder_sent_at)
