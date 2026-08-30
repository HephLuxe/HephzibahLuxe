"""
apps/inquiries/tests.py

Behavioural tests for the inquiries app. Run with:
    pytest apps/inquiries

Covers the one public write path in the project (POST /api/v1/inquiries/), the
staff surface behind it (list, detail, status), the admin's CSV export action
(including its CSV-injection guard), and the DB-level CheckConstraint that a
preferred end date can't precede the start date.

Two things about this app make a naive test useless, so both are handled here
explicitly:

* ``notifications.services._send_via_brevo`` returns immediately when
  ``settings.TESTING`` is set. A test that asserts "an email went out" without
  patching it passes while sending nothing, so every assertion about email
  content patches that function and inspects its call kwargs (to_email,
  template_id, params). Background dispatch is inline under the test runner
  (settings.BACKGROUND_EAGER, and no test process calls background.enable_async),
  so queue_notification -> send_notification_task -> send_now runs inline.

* ``services.create_inquiry`` claims a 120-second dedupe key in the shared
  cache — the same cache django-ratelimit and DRF's AnonRateThrottle count in.
  The key is a fingerprint of the WHOLE submission, so only a test that posts an
  identical payload twice can collide; left behind, that key turns the second
  submit into a silent no-op. The chosen defence is ``cache.clear()`` in setUp
  (InquiryAPITestCase below) rather than varying a field per test: it also resets
  the anon-throttle counter, which the multi-request validation tests would
  otherwise walk into.
"""

import csv
import datetime
import io
import json
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import requests
from django.conf import settings
from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone
from django_ratelimit.core import _split_rate
from rest_framework.test import APIClient

from apps.accounts.views import MyTokenObtainPairView
from apps.core.pagination import InquiryPageNumberPagination
from apps.core.throttling import ClientIPAnonRateThrottle
from apps.core.utils import user_display_name
from apps.inquiries import dedupe, recaptcha, services, views
from apps.inquiries.admin import EXPORT_FIELDS, InquiryFormAdmin
from apps.inquiries.models import InquiryForm
from apps.notifications.models import Notification

User = get_user_model()

# Patch target for every email assertion — see the module docstring.
BREVO = "apps.notifications.services._send_via_brevo"

SUBMIT_URL = "/api/v1/inquiries/"

ACK_DETAIL = "Your inquiry has been received. We'll be in touch within 2 business days."


def payload(**overrides) -> dict:
    """A submission the serializer accepts in full. Dates are relative so the
    fixture can't rot into "start date is in the past" a year from now."""
    start = timezone.localdate() + datetime.timedelta(days=180)
    body = {
        "first_name": "Ada",
        "last_name": "Obi",
        "email": "ada@example.com",
        "phone_number": "+2348012345678",
        "contact_mode": "Email",
        "event_type": "Wedding",
        "preferred_start_date": start.isoformat(),
        "preferred_end_date": (start + datetime.timedelta(days=2)).isoformat(),
        "desired_location": "Lagos",
        "budget": "2500000.00",
        "details": "A garden wedding for 150 guests.",
    }
    body.update(overrides)
    return body


def make_staff(email: str, *, alerts: bool = True, active: bool = True) -> "User":
    """A staff account, optionally flagged for lead alerts / deactivated.
    Both flags are set after create_user because User.save() derives is_staff
    from role on every save."""
    user = User.objects.create_user(
        first_name="Staff", last_name="Member", email=email, password="x", role="staff",
    )
    user.receives_inquiry_alerts = alerts
    user.is_active = active
    user.save()
    return user


class InquiryAPITestCase(TestCase):
    """Shared base: a DRF client (the routes are exercised through the URLconf,
    not by calling the view functions, because /inquiries/ is a method
    dispatcher and the rate limit lives on the URL) and a cache reset."""

    client_class = APIClient

    def setUp(self):
        cache.clear()  # dedupe keys + DRF anon-throttle counters
        self.addCleanup(cache.clear)


class InquiryDateRangeConstraintTests(TestCase):
    def test_end_before_start_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            InquiryForm.objects.create(
                first_name="A", last_name="B", email="a@b.com", phone_number="123",
                desired_location="Lagos",
                preferred_start_date=datetime.date(2027, 6, 10),
                preferred_end_date=datetime.date(2027, 6, 1),
            )

    def test_valid_range_is_accepted(self):
        obj = InquiryForm.objects.create(
            first_name="A", last_name="B", email="a@b.com", phone_number="123",
            desired_location="Lagos",
            preferred_start_date=datetime.date(2027, 6, 1),
            preferred_end_date=datetime.date(2027, 6, 10),
        )
        self.assertIsNotNone(obj.pk)


class SubmitInquiryTests(InquiryAPITestCase):
    """The public POST: what it stores and what it says back."""

    def test_valid_submission_returns_the_fixed_201_body(self):
        response = self.client.post(SUBMIT_URL, payload(), format="json")

        self.assertEqual(response.status_code, 201)
        # Equality, not membership: the body is a fixed acknowledgement and must
        # never echo the stored row (no id) back to an unauthenticated caller.
        self.assertEqual(response.json(), {"detail": ACK_DETAIL})
        self.assertNotIn("id", response.json())

    def test_valid_submission_stores_exactly_one_row(self):
        self.client.post(SUBMIT_URL, payload(), format="json")

        self.assertEqual(InquiryForm.objects.count(), 1)
        inquiry = InquiryForm.objects.get()
        self.assertIsInstance(inquiry.pk, uuid.UUID)
        self.assertIsNotNone(inquiry.created_at)
        self.assertEqual(inquiry.status, InquiryForm.Status.NEW)
        self.assertEqual(inquiry.first_name, "Ada")
        self.assertEqual(inquiry.email, "ada@example.com")
        self.assertEqual(inquiry.contact_mode, "Email")
        self.assertEqual(inquiry.event_type, "Wedding")
        self.assertEqual(inquiry.desired_location, "Lagos")

    def test_status_cannot_be_set_from_a_public_submit(self):
        response = self.client.post(SUBMIT_URL, payload(status="converted"), format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(InquiryForm.objects.get().status, InquiryForm.Status.NEW)


class InquiryDedupeWindowTests(InquiryAPITestCase):
    """
    The 120s window keys on a fingerprint of the WHOLE submission, not on the
    email. These tests pin both halves of that, because only one of them was
    true before: identical payloads still collapse, and a submission that differs
    in ANY single field is its own lead.

    The second half is the point. The old email-only key silently destroyed a
    lead who resubmitted inside the window with a corrected date — they got the
    same 201 and no row was written. A test class of its own because that
    regression is invisible from the response: every case here returns 201, so
    the assertions have to be on rows and notifications.

    Every test patches BREVO — without it the send is a no-op under TESTING and
    the notification assertions are void (module docstring).
    """

    def _submit(self, **overrides):
        return self.client.post(SUBMIT_URL, payload(**overrides), format="json")

    def _client_ack_count(self) -> int:
        return Notification.objects.filter(template_name="inquiry_received").count()

    def test_identical_payload_twice_is_one_lead(self):
        with patch(BREVO):
            first = self._submit()
            second = self._submit()

        # A real double-click resubmits identical form state, so the strict key
        # still catches the case the window exists for.
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json(), second.json())
        self.assertEqual(InquiryForm.objects.count(), 1)
        self.assertEqual(self._client_ack_count(), 1)

    def test_a_corrected_date_inside_the_window_is_its_own_lead(self):
        """The regression the rework exists for: under the email-only key this
        second submission was dropped and the correction lost."""
        start = timezone.localdate() + datetime.timedelta(days=180)
        corrected = start + datetime.timedelta(days=7)

        with patch(BREVO):
            self._submit()
            second = self._submit(
                preferred_start_date=corrected.isoformat(),
                preferred_end_date=(corrected + datetime.timedelta(days=2)).isoformat(),
            )

        self.assertEqual(second.status_code, 201)
        self.assertEqual(InquiryForm.objects.count(), 2)
        # Both leads acknowledged: the client is told about the correction too.
        self.assertEqual(self._client_ack_count(), 2)
        self.assertEqual(
            set(InquiryForm.objects.values_list("preferred_start_date", flat=True)),
            {start, corrected},
        )

    def test_a_changed_brief_inside_the_window_is_its_own_lead(self):
        with patch(BREVO):
            self._submit()
            second = self._submit(details="Actually a garden wedding for 300 guests.")

        self.assertEqual(second.status_code, 201)
        self.assertEqual(InquiryForm.objects.count(), 2)
        self.assertEqual(self._client_ack_count(), 2)

    def test_a_second_event_from_one_email_is_its_own_lead(self):
        """One email is not one lead: a planner (or a client with two events)
        submits twice from the same address, and both are real."""
        with patch(BREVO):
            self._submit()
            second = self._submit(event_type="Birthday", desired_location="Abuja")

        self.assertEqual(second.status_code, 201)
        self.assertEqual(InquiryForm.objects.count(), 2)
        self.assertEqual(
            set(InquiryForm.objects.values_list("event_type", flat=True)),
            {"Wedding", "Birthday"},
        )

    def test_every_single_field_change_defeats_the_window(self):
        """Field-by-field, so a future canonicalisation that accidentally drops
        a field from the fingerprint fails here rather than in production."""
        start = timezone.localdate() + datetime.timedelta(days=180)
        changes = {
            "first_name": "Adaeze",
            "last_name": "Okafor",
            "email": "ada.other@example.com",
            "phone_number": "+2348099999999",
            "contact_mode": "Phone Number",
            "event_type": "Birthday",
            # Earlier, not later: start + 30 would land past preferred_end_date
            # and be rejected as an inverted range before dedupe is even reached.
            "preferred_start_date": (start - datetime.timedelta(days=30)).isoformat(),
            "preferred_end_date": (start + datetime.timedelta(days=5)).isoformat(),
            "desired_location": "Abuja",
            "budget": "3000000.00",
            "details": "A different brief entirely.",
        }

        for field, value in changes.items():
            with self.subTest(field=field):
                cache.clear()
                InquiryForm.objects.all().delete()

                with patch(BREVO):
                    self._submit()
                    second = self._submit(**{field: value})

                self.assertEqual(second.status_code, 201)
                self.assertEqual(
                    InquiryForm.objects.count(), 2,
                    f"changing {field} must produce a second lead",
                )

    def test_an_omitted_budget_and_an_explicit_null_budget_dedupe(self):
        """budget is the one non-required field, so these two spellings of "not
        specified" reach create_inquiry as absent-key vs None. They mean the same
        thing to the lead and must land on one fingerprint."""
        body = payload()
        del body["budget"]

        with patch(BREVO):
            first = self.client.post(SUBMIT_URL, body, format="json")
            second = self.client.post(SUBMIT_URL, payload(budget=None), format="json")

        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(InquiryForm.objects.count(), 1)
        self.assertIsNone(InquiryForm.objects.get().budget)

    def test_the_same_budget_spelled_two_ways_dedupes(self):
        """5000 and 5000.00 are one budget. Pinned because a frontend that trims
        (or pads) trailing zeros between the click and the retry would otherwise
        write a duplicate lead."""
        with patch(BREVO):
            first = self._submit(budget="2500000")
            second = self._submit(budget="2500000.00")

        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(InquiryForm.objects.count(), 1)

    def test_the_email_is_matched_case_insensitively(self):
        """The email is an identity, not free text, so case is not a new lead —
        the remaining text fields stay case-sensitive by design."""
        with patch(BREVO):
            self._submit(email="ada@example.com")
            second = self._submit(email="ADA@Example.com")

        self.assertEqual(second.status_code, 201)
        self.assertEqual(InquiryForm.objects.count(), 1)

    def test_the_fingerprint_does_not_depend_on_key_order(self):
        """sort_keys, asserted directly: the same fields in a different dict
        order are the same submission."""
        from apps.inquiries.services import _dedupe_key

        forward = {"email": "ada@example.com", "details": "x", "budget": Decimal("1.00")}
        reversed_order = {"budget": Decimal("1.00"), "details": "x", "email": "ada@example.com"}

        self.assertEqual(_dedupe_key(forward), _dedupe_key(reversed_order))

    def test_a_failed_submit_releases_the_claim(self):
        """The claim is taken BEFORE the row exists, so a failure inside
        create_inquiry must release it — otherwise the lead's retry is swallowed
        as a duplicate for the full window and the lead is lost.

        Driven through the service, not the URL: the failure has to be observed
        as the raised exception, and the custom DRF exception handler would
        convert it to a 500 response before the test could see it."""
        from apps.inquiries import services

        # The shape create_inquiry receives from the view: validated, with
        # recaptcha_token already popped and dates/Decimals as Python objects.
        start = timezone.localdate() + datetime.timedelta(days=180)
        validated = {
            "first_name": "Ada", "last_name": "Obi", "email": "ada@example.com",
            "phone_number": "+2348012345678", "contact_mode": "Email",
            "event_type": "Wedding", "preferred_start_date": start,
            "preferred_end_date": start + datetime.timedelta(days=2),
            "desired_location": "Lagos", "budget": Decimal("2500000.00"),
            "details": "A garden wedding for 150 guests.",
        }

        with patch(
            "apps.notifications.services.queue_notification",
            side_effect=RuntimeError("notification queue exploded"),
        ):
            with self.assertRaises(RuntimeError):
                services.create_inquiry(validated)

        # The claim must be gone, so the identical retry is a fresh submission
        # rather than a silent no-op.
        self.assertIsNone(cache.get(services._dedupe_key(validated)))

        with patch(BREVO):
            retry = services.create_inquiry(dict(validated))

        self.assertIsNotNone(retry, "the retry was swallowed as a duplicate")
        self.assertEqual(self._client_ack_count(), 1)

    def test_a_repeat_submission_is_logged_so_the_rate_is_measurable(self):
        """A swallowed submit writes no row and sends no email, so this log line
        is its only trace — and the signal docs/INQUIRY_V2_BACKLOG.md §7 (R5)
        asks for before the rate-limit interaction is retuned."""
        with patch(BREVO):
            self._submit()
            with self.assertLogs("apps.inquiries.services", level="INFO") as logs:
                self._submit()

        self.assertTrue(
            any("dedupe window" in line for line in logs.output),
            logs.output,
        )


class InquiryEmailTests(InquiryAPITestCase):
    """What actually reaches Brevo. Every test here patches _send_via_brevo —
    without it the send is a no-op under TESTING and the assertions are void."""

    def setUp(self):
        super().setUp()
        self.alice = make_staff("alerts-alice@example.com")
        self.bob = make_staff("alerts-bob@example.com")

    @staticmethod
    def _calls_by_recipient(mock) -> dict:
        return {call.kwargs["to_email"]: call.kwargs for call in mock.call_args_list}

    def test_one_client_email_plus_one_per_flagged_staff(self):
        with patch(BREVO) as send:
            response = self.client.post(SUBMIT_URL, payload(), format="json")

        self.assertEqual(response.status_code, 201)
        # 1 + N: one acknowledgement, one internal alert per flagged staff member.
        self.assertEqual(Notification.objects.count(), 3)
        self.assertEqual(
            set(Notification.objects.values_list("recipient_email", flat=True)),
            {"ada@example.com", self.alice.email, self.bob.email},
        )
        self.assertEqual(
            set(self._calls_by_recipient(send)),
            {"ada@example.com", self.alice.email, self.bob.email},
        )
        # One send per address, never one multi-recipient send.
        self.assertEqual(send.call_count, 3)

    def test_client_acknowledgement_carries_only_the_first_name(self):
        with patch(BREVO) as send:
            self.client.post(SUBMIT_URL, payload(), format="json")

        client_call = self._calls_by_recipient(send)["ada@example.com"]
        self.assertEqual(client_call["template_id"], settings.BREVO_TEMPLATE_INQUIRY_RECEIVED)
        # Equality on the WHOLE dict, deliberately: this is the invariant that
        # catches a future change leaking the lead's own event/budget/contact
        # details back into their acknowledgement. A membership check would not.
        self.assertEqual(client_call["params"], {"first_name": "Ada"})

        stored = Notification.objects.get(template_name="inquiry_received")
        self.assertEqual(stored.context, {"first_name": "Ada"})
        self.assertIsNone(stored.recipient_user)

    def test_internal_alert_carries_the_lead_detail_staff_need(self):
        with patch(BREVO) as send:
            self.client.post(SUBMIT_URL, payload(), format="json")

        inquiry = InquiryForm.objects.get()
        internal = self._calls_by_recipient(send)[self.alice.email]
        self.assertEqual(
            internal["template_id"], settings.BREVO_TEMPLATE_INQUIRY_SUBMITTED_INTERNAL
        )
        params = internal["params"]
        self.assertEqual(params["inquiry_id"], str(inquiry.id))
        # Present, serialised to a parseable ISO string and the right instant —
        # but NOT asserted to microsecond equality between the in-memory value
        # and the Postgres round-trip, which is brittle for no added coverage.
        submitted_at = datetime.datetime.fromisoformat(params["submitted_at"])
        self.assertEqual(submitted_at.date(), inquiry.created_at.date())
        self.assertLess(
            abs(submitted_at - inquiry.created_at), datetime.timedelta(seconds=1)
        )
        self.assertEqual(params["budget"], "2500000.00")
        self.assertEqual(params["recipient_name"], self.alice.first_name)
        self.assertEqual(params["email"], "ada@example.com")
        self.assertEqual(params["details"], "A garden wedding for 150 guests.")

        # recipient_user is the durable FK, so the row still resolves to this
        # staff member after an email change.
        row = Notification.objects.get(
            template_name="inquiry_submitted_internal", recipient_email=self.alice.email
        )
        self.assertEqual(row.recipient_user, self.alice)


class InquiryRecipientResolutionTests(InquiryAPITestCase):
    """Who counts as "flagged staff": receives_inquiry_alerts AND is_active AND
    is_staff — all three."""

    def test_flagged_client_and_deactivated_staff_are_both_excluded(self):
        active_staff = make_staff("resolve-active@example.com")

        # role=client, so User.save() forces is_staff=False even with the flag on.
        flagged_client = User.objects.create_user(
            first_name="Cee", last_name="Lient", email="resolve-client@example.com",
            password="x",
        )
        flagged_client.receives_inquiry_alerts = True
        flagged_client.save()

        deactivated_staff = make_staff("resolve-gone@example.com", active=False)

        with patch(BREVO) as send:
            response = self.client.post(SUBMIT_URL, payload(), format="json")

        self.assertEqual(response.status_code, 201)
        internal_recipients = set(
            Notification.objects.filter(
                template_name="inquiry_submitted_internal"
            ).values_list("recipient_email", flat=True)
        )
        self.assertEqual(internal_recipients, {active_staff.email})
        self.assertNotIn(flagged_client.email, internal_recipients)
        self.assertNotIn(deactivated_staff.email, internal_recipients)
        self.assertEqual(send.call_count, 2)  # client ack + the one active staff

    def test_no_flagged_staff_still_201_and_emits_the_signal(self):
        with patch(BREVO), self.assertLogs("apps.inquiries.services", level="ERROR") as logs:
            response = self.client.post(SUBMIT_URL, payload(), format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(InquiryForm.objects.count(), 1)
        self.assertEqual(
            Notification.objects.filter(template_name="inquiry_received").count(), 1
        )
        self.assertEqual(
            Notification.objects.filter(template_name="inquiry_submitted_internal").count(), 0
        )
        self.assertTrue(
            any(getattr(record, "event", None) == "inquiry_no_recipients"
                for record in logs.records),
            f"expected an inquiry_no_recipients log record, got {[r.msg for r in logs.records]}",
        )


class InquiryValidationTests(InquiryAPITestCase):
    """400s, not 500s and not half-filled leads."""

    # Every field the live form collects. The last six are nullable/blank on the
    # model and pinned required by the serializer — they are the whole point of
    # this loop.
    REQUIRED_FIELDS = [
        "first_name", "last_name", "email", "phone_number",
        "contact_mode", "event_type", "preferred_start_date",
        "preferred_end_date", "desired_location", "details",
    ]

    def test_end_before_start_is_a_400_not_a_500(self):
        start = timezone.localdate() + datetime.timedelta(days=180)
        response = self.client.post(
            SUBMIT_URL,
            payload(
                preferred_start_date=start.isoformat(),
                preferred_end_date=(start - datetime.timedelta(days=1)).isoformat(),
            ),
            format="json",
        )

        # Without serializer.validate() this reaches the DB CheckConstraint and
        # surfaces as a 500 internal_error.
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("preferred_end_date", body["errors"])
        self.assertEqual(InquiryForm.objects.count(), 0)

    def test_each_required_field_is_rejected_when_missing(self):
        for field in self.REQUIRED_FIELDS:
            with self.subTest(missing=field):
                body = payload()
                body.pop(field)
                response = self.client.post(SUBMIT_URL, body, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "validation_error")
                self.assertIn(field, response.json()["errors"])
        self.assertEqual(InquiryForm.objects.count(), 0)

    def test_each_required_field_is_rejected_when_explicitly_null(self):
        # `required=True` alone only rejects an ABSENT key; the model's null=True
        # fields would still accept an explicit null without allow_null=False.
        for field in self.REQUIRED_FIELDS:
            with self.subTest(null=field):
                response = self.client.post(SUBMIT_URL, payload(**{field: None}), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json()["errors"])
        self.assertEqual(InquiryForm.objects.count(), 0)

    def test_blank_strings_are_rejected_on_the_blank_true_fields(self):
        # details/contact_mode/event_type are blank=True on the model, so DRF
        # inherits allow_blank=True and "" was stored as a half-filled lead.
        for field in ["details", "contact_mode", "event_type"]:
            with self.subTest(blank=field):
                response = self.client.post(SUBMIT_URL, payload(**{field: ""}), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json()["errors"])
        self.assertEqual(InquiryForm.objects.count(), 0)

    def test_details_over_the_length_bound_is_rejected(self):
        # details is a TextField, which carries no bound of its own — before the
        # serializer's max_length the only ceiling was Django's 2.5MB
        # DATA_UPLOAD_MAX_MEMORY_SIZE, and one oversized brief is copied into a
        # Notification.context row per flagged staff member on top of the lead.
        response = self.client.post(SUBMIT_URL, payload(details="x" * 4001), format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertIn("details", response.json()["errors"])
        self.assertEqual(InquiryForm.objects.count(), 0)

    def test_details_exactly_at_the_length_bound_is_accepted(self):
        # Pins the boundary as inclusive: a lead who fills the field to the limit
        # the frontend advertises must not be rejected by an off-by-one.
        brief = "x" * 4000
        with patch(BREVO):
            response = self.client.post(SUBMIT_URL, payload(details=brief), format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(InquiryForm.objects.get().details, brief)

    def test_start_date_in_the_past_is_rejected(self):
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        response = self.client.post(
            SUBMIT_URL,
            payload(
                preferred_start_date=yesterday.isoformat(),
                preferred_end_date=(yesterday + datetime.timedelta(days=2)).isoformat(),
            ),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("preferred_start_date", response.json()["errors"])
        self.assertEqual(InquiryForm.objects.count(), 0)

    def test_start_date_of_today_is_accepted(self):
        today = timezone.localdate()
        with patch(BREVO):
            response = self.client.post(
                SUBMIT_URL,
                payload(
                    preferred_start_date=today.isoformat(),
                    preferred_end_date=(today + datetime.timedelta(days=2)).isoformat(),
                ),
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(InquiryForm.objects.get().preferred_start_date, today)


def google_says(**body) -> MagicMock:
    """A stand-in for the siteverify HTTP response carrying `body` as its JSON."""
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = body
    return response


POST = "apps.inquiries.recaptcha.requests.post"
SECRET = dict(RECAPTCHA_SECRET_KEY="test-secret")


@override_settings(**SECRET)
class RecaptchaV3Tests(TestCase):
    """The v3 verdict is `score`, not `success`.

    None of this was reachable before: RECAPTCHA_SECRET_KEY is blank in dev/CI,
    so verify_recaptcha returned True on line one and the whole module was
    untested. These call it directly with the secret overridden.
    """

    def verify(self, response, *, action=recaptcha.ACTION_SUBMIT_INQUIRY, **kwargs):
        with patch(POST, return_value=response) as post:
            self.post = post
            return recaptcha.verify_recaptcha("token", action=action, **kwargs)

    def test_low_score_is_rejected_despite_success_true(self):
        # THE v3 test. `success: true` only means the token parsed and matched
        # this site key — a headless browser gets one. Reading only `success`
        # accepts every bot, which is what a v2-shaped integration does.
        allowed = self.verify(google_says(
            success=True, score=0.1, action=recaptcha.ACTION_SUBMIT_INQUIRY,
        ))
        self.assertFalse(allowed)

    def test_high_score_is_accepted(self):
        allowed = self.verify(google_says(
            success=True, score=0.9, action=recaptcha.ACTION_SUBMIT_INQUIRY,
        ))
        self.assertTrue(allowed)

    def test_score_exactly_at_the_threshold_is_accepted(self):
        # The bound is inclusive: >= threshold passes.
        with override_settings(RECAPTCHA_MIN_SCORES={"submit_inquiry": 0.5}):
            allowed = self.verify(google_says(
                success=True, score=0.5, action=recaptcha.ACTION_SUBMIT_INQUIRY,
            ))
        self.assertTrue(allowed)

    def test_action_mismatch_is_rejected(self):
        # One site key covers every form on the domain, so without this check a
        # token minted on a public page is replayable against any other endpoint
        # sharing the key. A perfect score does not save it.
        allowed = self.verify(google_says(success=True, score=1.0, action="login"))
        self.assertFalse(allowed)

    def test_success_false_is_rejected(self):
        allowed = self.verify(google_says(success=False, **{"error-codes": ["timeout-or-duplicate"]}))
        self.assertFalse(allowed)

    def test_a_v2_response_with_no_score_is_accepted(self):
        # A v2 secret answers with no score and no action. That is a
        # provisioning mistake, not an attack, and for v2 `success` genuinely IS
        # the verdict — so this accepts rather than rejecting every submission,
        # and logs at ERROR so the misconfiguration is visible.
        with self.assertLogs("apps.inquiries.recaptcha", level="ERROR"):
            allowed = self.verify(google_says(success=True))
        self.assertTrue(allowed)

    def test_network_failure_fails_open(self):
        # Deliberate: losing a real lead to a Google outage is worse than
        # accepting a spam one, and the endpoint is rate-limited regardless.
        with patch(POST, side_effect=requests.RequestException("boom")):
            allowed = recaptcha.verify_recaptcha(
                "token", action=recaptcha.ACTION_SUBMIT_INQUIRY
            )
        self.assertTrue(allowed)

    def test_remote_ip_is_forwarded_to_google(self):
        self.verify(
            google_says(success=True, score=0.9, action=recaptcha.ACTION_SUBMIT_INQUIRY),
            remote_ip="203.0.113.7",
        )
        self.assertEqual(self.post.call_args.kwargs["data"]["remoteip"], "203.0.113.7")

    def test_remote_ip_is_omitted_when_unknown(self):
        # Sent empty, Google treats it as a malformed request; it is an optional
        # hint, so it is left out instead.
        self.verify(google_says(success=True, score=0.9, action=recaptcha.ACTION_SUBMIT_INQUIRY))
        self.assertNotIn("remoteip", self.post.call_args.kwargs["data"])

    def test_unregistered_action_falls_back_to_the_default_threshold(self):
        with override_settings(RECAPTCHA_MIN_SCORES={}, RECAPTCHA_MIN_SCORE_DEFAULT=0.7):
            self.assertEqual(recaptcha.min_score_for("some_new_form"), 0.7)

    def test_no_secret_skips_verification_entirely(self):
        # The env gate that keeps dev/CI/tests config-free — Google is never
        # called at all.
        with override_settings(RECAPTCHA_SECRET_KEY=""), patch(POST) as post:
            allowed = recaptcha.verify_recaptcha(
                "anything", action=recaptcha.ACTION_SUBMIT_INQUIRY
            )
        self.assertTrue(allowed)
        post.assert_not_called()


@override_settings(**SECRET)
class RecaptchaEndpointTests(InquiryAPITestCase):
    """The score gate as seen through POST /api/v1/inquiries/."""

    def test_a_bot_score_is_a_400_with_no_lead_and_no_email(self):
        with patch(BREVO) as brevo, patch(POST, return_value=google_says(
            success=True, score=0.1, action=recaptcha.ACTION_SUBMIT_INQUIRY,
        )):
            response = self.client.post(
                SUBMIT_URL, payload(recaptcha_token="t"), format="json"
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertEqual(InquiryForm.objects.count(), 0)
        brevo.assert_not_called()

    def test_a_human_score_submits_normally(self):
        with patch(BREVO), patch(POST, return_value=google_says(
            success=True, score=0.9, action=recaptcha.ACTION_SUBMIT_INQUIRY,
        )) as post:
            response = self.client.post(
                SUBMIT_URL, payload(recaptcha_token="t"), format="json"
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(InquiryForm.objects.count(), 1)
        # The view sends the action the frontend must mint the token with.
        self.assertEqual(post.call_args.kwargs["data"]["response"], "t")

    def test_a_missing_token_is_rejected_without_calling_google(self):
        with patch(POST) as post:
            response = self.client.post(SUBMIT_URL, payload(), format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(InquiryForm.objects.count(), 0)
        post.assert_not_called()


class InquiryBudgetTests(InquiryAPITestCase):
    def setUp(self):
        super().setUp()
        self.staff = make_staff("budget-staff@example.com")

    def test_large_budget_round_trips_at_full_precision(self):
        with patch(BREVO) as send:
            response = self.client.post(
                SUBMIT_URL, payload(budget="150000000.00"), format="json"
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(InquiryForm.objects.get().budget, Decimal("150000000.00"))
        internal = next(
            call.kwargs for call in send.call_args_list
            if call.kwargs["to_email"] == self.staff.email
        )
        # Decimal -> str via _serialise_context; the template adds the ₦ itself.
        self.assertEqual(internal["params"]["budget"], "150000000.00")

    def test_null_budget_is_accepted_and_reads_not_specified_internally(self):
        with patch(BREVO) as send:
            response = self.client.post(SUBMIT_URL, payload(budget=None), format="json")

        self.assertEqual(response.status_code, 201)
        self.assertIsNone(InquiryForm.objects.get().budget)
        internal = next(
            call.kwargs for call in send.call_args_list
            if call.kwargs["to_email"] == self.staff.email
        )
        self.assertEqual(internal["params"]["budget"], "Not specified")


class InquiryStaffSurfaceTests(InquiryAPITestCase):
    """List / detail / status: staff-gated, read-only apart from status."""

    def setUp(self):
        super().setUp()
        self.staff = make_staff("surface-staff@example.com", alerts=False)
        self.client_user = User.objects.create_user(
            first_name="Cee", last_name="Lient", email="surface-client@example.com",
            password="x",
        )
        start = timezone.localdate() + datetime.timedelta(days=90)
        self.wedding = InquiryForm.objects.create(
            first_name="Ada", last_name="Obi", email="ada@example.com",
            phone_number="+2348010000001", contact_mode="Email", event_type="Wedding",
            preferred_start_date=start, preferred_end_date=start + datetime.timedelta(days=1),
            desired_location="Lagos", budget=Decimal("2500000.00"), details="Garden wedding.",
        )
        self.birthday = InquiryForm.objects.create(
            first_name="Bola", last_name="Ade", email="bola@example.com",
            phone_number="+2348010000002", contact_mode="Phone Number", event_type="Birthday",
            preferred_start_date=start, preferred_end_date=start + datetime.timedelta(days=1),
            desired_location="Abuja", details="Fiftieth birthday.",
            status=InquiryForm.Status.CONTACTED,
        )
        self.corporate = InquiryForm.objects.create(
            first_name="Chidi", last_name="Eze", email="chidi@example.com",
            phone_number="+2348010000003", contact_mode="Email", event_type="Corporate",
            preferred_start_date=start, preferred_end_date=start + datetime.timedelta(days=1),
            desired_location="Lagos", details="Annual dinner.",
        )
        self.detail_url = reverse("inquiry_detail", args=[self.wedding.id])
        self.status_url = reverse("update_inquiry_status", args=[self.wedding.id])

    def _as_staff(self) -> APIClient:
        client = APIClient()
        client.force_authenticate(user=self.staff)
        return client

    def _as_client_user(self) -> APIClient:
        client = APIClient()
        client.force_authenticate(user=self.client_user)
        return client

    def test_anonymous_gets_401_on_every_staff_route(self):
        self.assertEqual(self.client.get(SUBMIT_URL).status_code, 401)
        self.assertEqual(self.client.get(self.detail_url).status_code, 401)
        self.assertEqual(
            self.client.patch(self.status_url, {"status": "contacted"}, format="json").status_code,
            401,
        )

    def test_client_account_gets_403_on_every_staff_route(self):
        client = self._as_client_user()
        self.assertEqual(client.get(SUBMIT_URL).status_code, 403)
        self.assertEqual(client.get(self.detail_url).status_code, 403)
        self.assertEqual(
            client.patch(self.status_url, {"status": "contacted"}, format="json").status_code, 403
        )

    def test_staff_lists_every_lead_newest_first(self):
        response = self._as_staff().get(SUBMIT_URL)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 3)
        self.assertEqual(len(body["results"]), 3)
        self.assertEqual(body["results"][0]["id"], str(self.corporate.id))

    def test_the_list_is_always_paginated(self):
        """No single request returns the whole lead table.

        This used to be opt-in: without ?page the response serialised EVERY lead
        — names, emails, phone numbers, budgets — in one payload. A rate limit
        cannot help with that; it caps how many requests a caller makes, not how
        much each one hands over, so a compromised staff token needed exactly one.
        Bounding the page is the control that applies.

        Page size is read from the paginator rather than hardcoded so the number
        stays a UI decision.
        """
        page_size = InquiryPageNumberPagination.page_size
        # Enough leads to guarantee a second page.
        for index in range(page_size + 2):
            InquiryForm.objects.create(
                first_name=f"Lead{index}", last_name="Extra",
                email=f"lead{index}@example.com", phone_number="+2348000000000",
                desired_location="Lagos",
            )

        body = self._as_staff().get(SUBMIT_URL).json()

        self.assertEqual(len(body["results"]), page_size)
        self.assertLess(len(body["results"]), body["count"])
        # The rest is reachable, it just costs another request.
        self.assertIsNotNone(body["next"])

    def test_a_caller_can_widen_the_page_but_only_to_the_paginator_ceiling(self):
        """?page_size= still works, so nothing became unreachable — but it is
        capped, which is what keeps the bound meaningful."""
        over_the_cap = InquiryPageNumberPagination.max_page_size + 100
        body = self._as_staff().get(SUBMIT_URL, {"page_size": over_the_cap}).json()

        self.assertLessEqual(
            len(body["results"]), InquiryPageNumberPagination.max_page_size
        )

    def test_the_lead_inbox_page_size_is_its_own(self):
        """Sized for this list, not inherited. The shared paginator's 7 is pinned
        to the Budget Payment History Figma spec, so resizing the lead inbox must
        not move that table — and vice versa."""
        from apps.core.pagination import StandardPageNumberPagination

        self.assertEqual(InquiryPageNumberPagination.page_size, 10)
        self.assertNotEqual(
            InquiryPageNumberPagination.page_size,
            StandardPageNumberPagination.page_size,
        )
        # Same envelope and query params as the rest of the portal, though.
        self.assertTrue(
            issubclass(InquiryPageNumberPagination, StandardPageNumberPagination)
        )

    def test_filters_narrow_the_result_set(self):
        client = self._as_staff()

        self.assertEqual(client.get(SUBMIT_URL, {"status": "new"}).json()["count"], 2)
        self.assertEqual(client.get(SUBMIT_URL, {"status": "contacted"}).json()["count"], 1)
        self.assertEqual(client.get(SUBMIT_URL, {"event_type": "Wedding"}).json()["count"], 1)
        self.assertEqual(client.get(SUBMIT_URL, {"search": "Abuja"}).json()["count"], 1)
        self.assertEqual(client.get(SUBMIT_URL, {"search": "Lagos"}).json()["count"], 2)
        self.assertEqual(client.get(SUBMIT_URL, {"search": "chidi@example"}).json()["count"], 1)
        self.assertEqual(client.get(SUBMIT_URL, {"search": "nobody"}).json()["count"], 0)

    def test_ordering_outside_the_allow_list_is_rejected(self):
        response = self._as_staff().get(SUBMIT_URL, {"ordering": "budget"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_staff_reads_one_lead(self):
        response = self._as_staff().get(self.detail_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], str(self.wedding.id))
        self.assertEqual(response.json()["event_type_display"], "Wedding")

    def test_status_patch_changes_status_and_nothing_the_client_submitted(self):
        client = self._as_staff()
        before = client.get(self.detail_url).json()

        response = client.patch(self.status_url, {"status": "qualified"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "qualified")
        self.assertEqual(response.json()["status_display"], "Qualified")

        after = client.get(self.detail_url).json()
        self.assertEqual(after["status"], "qualified")
        # Attribution records the staff member who moved it — resolved from the
        # FK at read time, so a later rename of that account propagates here.
        self.assertEqual(after["last_updated_by_display"], user_display_name(self.staff))
        # created_by stays empty: leads arrive through the unauthenticated POST,
        # so no staff member ever "creates" one.
        self.assertEqual(after["created_by_display"], "")
        # Raw actor FK ids must never reach a client.
        self.assertNotIn("last_updated_by", after)
        self.assertNotIn("created_by", after)
        # updated_at + attribution move; everything the client typed is frozen.
        volatile = {"status", "status_display", "updated_at", "last_updated_by_display"}
        self.assertEqual(
            {k: v for k, v in after.items() if k not in volatile},
            {k: v for k, v in before.items() if k not in volatile},
        )
        self.assertNotEqual(after["updated_at"], before["updated_at"])

    def test_invalid_status_value_is_rejected(self):
        response = self._as_staff().patch(
            self.status_url, {"status": "not-a-status"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "validation_error")
        self.wedding.refresh_from_db()
        self.assertEqual(self.wedding.status, InquiryForm.Status.NEW)

    def test_missing_status_value_is_rejected(self):
        response = self._as_staff().patch(self.status_url, {}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_non_uuid_segment_never_reaches_a_view(self):
        # <uuid:inquiry_id> doesn't match "5", so the URLconf 404s before any
        # permission class runs.
        self.assertEqual(self._as_staff().get("/api/v1/inquiries/5/").status_code, 404)

    def test_unknown_uuid_is_404(self):
        url = reverse("inquiry_detail", args=[uuid.uuid4()])
        self.assertEqual(self._as_staff().get(url).status_code, 404)


class InquiryCsvExportTests(TestCase):
    """
    The admin's "Export selected inquiries to CSV" action.

    Driven by instantiating the ModelAdmin against a RequestFactory request —
    the same shape the export was verified in — rather than through the admin
    HTTP surface, which would add login plumbing and test Django's changelist
    instead of this action.
    """

    def setUp(self):
        self.model_admin = InquiryFormAdmin(InquiryForm, site)
        self.request = RequestFactory().post("/admin/inquiries/inquiryform/")
        self.request.user = make_staff("csv-admin@example.com", alerts=False)
        # message_user() writes to the messages framework, and RequestFactory
        # runs no middleware, so the storage is attached by hand. The session is
        # never read: the queued message is discarded with the request.
        self.request.session = "session"
        self.request._messages = FallbackStorage(self.request)

        start = timezone.localdate() + datetime.timedelta(days=90)
        self.dates = {"preferred_start_date": start,
                      "preferred_end_date": start + datetime.timedelta(days=1)}

    def _lead(self, **overrides) -> InquiryForm:
        fields = {
            "first_name": "Ada", "last_name": "Obi", "email": "ada@example.com",
            "phone_number": "+2348010000001", "contact_mode": "Email",
            "event_type": "Wedding", "desired_location": "Lagos",
            "budget": Decimal("2500000.00"), "details": "Garden wedding.",
            **self.dates,
        }
        fields.update(overrides)
        return InquiryForm.objects.create(**fields)

    def _export(self, queryset) -> list:
        """Run the action and return the parsed CSV rows (header first)."""
        response = self.model_admin.export_as_csv(self.request, queryset)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        # utf-8-sig drops the BOM the export writes for Excel on Windows.
        return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_header_lists_every_expected_column(self):
        self._lead()

        rows = self._export(InquiryForm.objects.all())

        self.assertEqual(rows[0], EXPORT_FIELDS)
        self.assertEqual(
            rows[0],
            ["id", "first_name", "last_name", "email", "phone_number", "contact_mode",
             "event_type", "desired_location", "preferred_start_date",
             "preferred_end_date", "budget", "details", "status", "created_at"],
        )

    def test_formula_prefixed_free_text_is_neutralised(self):
        # details is free text from anonymous strangers, and a cell starting with
        # any of these is EXECUTED by Excel/Sheets — the DDE payload below is a
        # working one. The leading apostrophe forces the cell to be text.
        payloads = [
            "=cmd|' /C calc'!A0",
            "+1+1",
            "-2+3",
            "@SUM(1+1)*cmd|' /C calc'!A0",
        ]
        for index, injected in enumerate(payloads):
            with self.subTest(payload=injected):
                InquiryForm.objects.all().delete()
                self._lead(details=injected, first_name=injected,
                           email=f"inject{index}@example.com")

                rows = self._export(InquiryForm.objects.all())

                cells = dict(zip(rows[0], rows[1]))
                self.assertEqual(cells["details"], "'" + injected)
                self.assertEqual(cells["first_name"], "'" + injected)
                self.assertFalse(cells["details"].startswith(("=", "+", "-", "@")))

    def test_ordinary_text_is_not_quoted(self):
        self._lead(details="Garden wedding.", first_name="Ada")

        cells = dict(zip(*self._export(InquiryForm.objects.all())[:2]))

        self.assertEqual(cells["details"], "Garden wedding.")
        self.assertEqual(cells["first_name"], "Ada")

    def test_only_the_queryset_it_is_handed_is_exported(self):
        # The action exists so staff can narrow the changelist and export exactly
        # that selection; exporting the whole table would defeat the point.
        wanted = self._lead(email="keep-a@example.com")
        also_wanted = self._lead(email="keep-b@example.com")
        self._lead(email="drop@example.com", status=InquiryForm.Status.LOST)
        self._lead(email="drop2@example.com", status=InquiryForm.Status.ARCHIVED)

        rows = self._export(InquiryForm.objects.filter(status=InquiryForm.Status.NEW))

        self.assertEqual(len(rows), 3)  # header + the two new leads
        exported_ids = {row[0] for row in rows[1:]}
        self.assertEqual(exported_ids, {str(wanted.id), str(also_wanted.id)})
        self.assertEqual(InquiryForm.objects.count(), 4)

    def test_null_budget_is_a_blank_cell_not_the_string_none(self):
        self._lead(budget=None)

        cells = dict(zip(*self._export(InquiryForm.objects.all())[:2]))

        self.assertEqual(cells["budget"], "")
        self.assertNotEqual(cells["budget"], "None")


@override_settings(RATELIMIT_ENABLE=True)
class InquiryRateLimitTests(InquiryAPITestCase):
    """
    The limiter is off under TESTING by design, so this opts back in.

    Full-stack on purpose: the limits are wrapped around the view AT THE URL
    (urls.py::_rl), so APIRequestFactory + a direct view call would not see them
    at all. self.client + the real path is the only shape that exercises them.

    TWO tiers are wired here and the tests below separate them, because they
    catch different callers:

      burst  3/10m  per (IP, email)  — a real person resubmitting
      flood  10/h   per IP alone     — a script varying the email

    The IP tier is the load-bearing one. A single "3/h per (IP, email)" limit
    looks strict and isn't: the email on a public form is chosen by whoever is
    posting and costs them nothing, so varying it bought a fresh bucket every
    time and one machine could submit without limit.
    """

    def setUp(self):
        super().setUp()  # cache.clear() — limiter counters AND dedupe keys
        # Freeze the clock: django-ratelimit's window is fixed, and a burst that
        # straddles a boundary resets the counter, letting the request that
        # should be blocked through (the flake fixed in e9b32d9).
        patcher = patch("django_ratelimit.core.time.time", return_value=1_700_000_000.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    # Derived from settings, never hardcoded. These tests assert the SHAPE of the
    # two tiers — a burst that stops a repeating submitter, a flood tier that
    # stops one varying the email — and that shape has to hold at whatever
    # numbers config/settings.py declares. Hardcoding "the 4th is blocked" made
    # four of them fail the moment the burst count moved from 3 to 4, which is
    # noise about the fixture rather than a real regression.
    BURST_COUNT, _ = _split_rate(settings.RATE_LIMITS["inquiry_submit_burst"])
    FLOOD_COUNT, _ = _split_rate(settings.RATE_LIMITS["inquiry_submit_ip"])

    def _post_from(self, client_ip, body):
        """Submit as a client at `client_ip`, arriving through the edge proxy.

        REMOTE_ADDR is a private platform address (a trusted hop) and the real
        client is the rightmost XFF entry — the shape every request has on
        Railway. Needed here because these tests must control the IP: the two
        tiers are distinguished by whether the IP is held constant or varied.
        """
        return self.client.post(
            SUBMIT_URL, body, format="json",
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_FOR=f"{client_ip}, 10.0.0.5",
        )

    def test_the_burst_tier_blocks_a_repeating_submitter_with_429(self):
        """Keyed on (IP, email), so this is one lead's own allowance.

        Each post carries a DIFFERENT `details`, i.e. a genuinely new submission
        each time. That matters: an identical repeat is a double-click and is
        deliberately NOT counted (see
        InquiryDedupeDoesNotCostAnAttemptTests), so sending the same body over
        and over would never trip this tier at all. What it caps is a lead who
        keeps submitting *changed* inquiries from one address.
        """
        for attempt in range(self.BURST_COUNT):
            response = self._post_from("41.2.3.4", payload(details=f"Take {attempt}"))
            self.assertEqual(response.status_code, 201, f"request {attempt + 1}")

        blocked = self._post_from("41.2.3.4", payload(details="One too many"))

        # 429, not 403: a 403 is what a limiter placed INSIDE DRF's dispatch
        # produces, and would mean the middleware never saw the Ratelimited
        # exception.
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["code"], "rate_limited")
        # Every accepted one was a distinct submission, so each wrote its lead.
        self.assertEqual(InquiryForm.objects.count(), self.BURST_COUNT)

    def test_the_burst_tier_is_per_email_so_a_second_lead_is_unaffected(self):
        """One person fumbling must not lock out the next genuine lead from the
        same office or shared connection."""
        for attempt in range(self.BURST_COUNT):
            self._post_from("41.2.3.4", payload(details=f"Take {attempt}"))
        self.assertEqual(
            self._post_from("41.2.3.4", payload(details="over")).status_code, 429
        )

        other = self._post_from("41.2.3.4", payload(email="someone-else@example.com"))
        self.assertEqual(other.status_code, 201)

    def test_varying_the_email_no_longer_buys_unlimited_submissions(self):
        """The gap the IP tier exists to close.

        Every request comes from ONE IP with a DIFFERENT email, so the burst
        tier never trips — each address is its own bucket. Without a per-IP
        limit this loop would run forever. RATE_LIMIT_INQUIRY_SUBMIT_IP=10/h
        stops it at ten.
        """
        for attempt in range(self.FLOOD_COUNT):
            response = self._post_from("41.2.3.4", payload(email=f"spam{attempt}@example.com"))
            self.assertEqual(response.status_code, 201, f"submission {attempt + 1}")

        blocked = self._post_from("41.2.3.4", payload(email="spam-final@example.com"))
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["code"], "rate_limited")
        self.assertEqual(InquiryForm.objects.count(), self.FLOOD_COUNT)

    def test_the_flood_tier_is_per_ip_so_another_client_is_unaffected(self):
        """The IP tier must not become a global ceiling: exhausting one address
        cannot silence every other lead in the world."""
        for attempt in range(self.FLOOD_COUNT):
            self._post_from("41.2.3.4", payload(email=f"spam{attempt}@example.com"))
        self.assertEqual(
            self._post_from("41.2.3.4", payload(email="more@example.com")).status_code, 429
        )

        elsewhere = self._post_from("41.2.3.99", payload(email="genuine@example.com"))
        self.assertEqual(elsewhere.status_code, 201)

    def test_tripping_the_burst_tier_does_not_spend_the_ip_allowance(self):
        """Tier ordering, observably.

        The burst tier is applied OUTERMOST, so when it blocks, the IP tier never
        increments. That matters: a lead who fumbles must not draw down the
        allowance that exists to catch a script. Fumble first, then confirm the
        full per-IP budget is still there.
        """
        # BURST_COUNT get through, then one more is refused by the burst tier.
        # Distinct payloads, because an identical repeat is not counted at all.
        for attempt in range(self.BURST_COUNT + 1):
            self._post_from("41.2.3.4", payload(details=f"Take {attempt}"))

        # The IP tier should have counted only the ones that got PAST the burst
        # tier, so the refused request cost nothing. Spend the remainder on
        # distinct emails, which keeps the burst tier out of the way.
        remaining = self.FLOOD_COUNT - self.BURST_COUNT
        for attempt in range(remaining):
            response = self._post_from("41.2.3.4", payload(email=f"lead{attempt}@example.com"))
            self.assertEqual(response.status_code, 201, f"submission {attempt + 1}")

        exhausted = self._post_from("41.2.3.4", payload(email="one-too-many@example.com"))
        self.assertEqual(exhausted.status_code, 429)

    def test_a_429_carries_retry_after(self):
        """A lead who gets a 429 needs something to back off on. django-ratelimit
        sent no Retry-After at all, while DRF's throttle always has — one API
        answering two different ways."""
        for attempt in range(self.BURST_COUNT):
            self._post_from("41.2.3.4", payload(details=f"Take {attempt}"))
        blocked = self._post_from("41.2.3.4", payload(details="over"))
        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(int(blocked.headers["Retry-After"]), 0)


class InquiryThrottleExemptionTests(InquiryAPITestCase):
    """
    Lead capture must not share a bucket with the rest of the public surface.

    DRF's anon throttle keys on the client only — the view is NOT part of its
    cache key — so every unauthenticated endpoint drew from one shared per-IP
    pool. A burst of failed logins or password-reset requests could therefore
    leave a genuine lead unable to submit at all, seeing a 429 caused entirely by
    traffic that had nothing to do with them. submit_inquiry carries
    @throttle_classes([]) so the only limits acting on it are the two chosen for
    it.

    RATELIMIT_ENABLE stays False here (the class-level override is deliberately
    absent) so the URL limiter is out of the way and any 429 observed could only
    have come from the DRF throttle.
    """

    def test_the_public_submit_is_opted_out_of_drf_throttling(self):
        """submit_inquiry carries NO throttle classes, while the project default
        is non-empty. Both halves matter: an empty default would make the first
        assertion true of every endpoint and prove nothing."""
        from apps.inquiries.views import submit_inquiry
        self.assertTrue(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"])
        self.assertEqual(submit_inquiry.cls.throttle_classes, [])

    def test_the_staff_list_keeps_the_project_wide_throttles(self):
        """Only the public POST opts out. The authenticated surface keeps the
        per-account burst ceiling like every other endpoint."""
        from apps.inquiries.views import list_inquiries
        names = [cls.__name__ for cls in list_inquiries.cls.throttle_classes]
        self.assertIn("UserBurstRateThrottle", names)

    def test_an_exhausted_anon_ceiling_does_not_block_a_lead(self):
        """The behaviour the exemption buys, end to end.

        The anon ceiling is squeezed to 3/day on the LOGIN view only and then
        spent — standing in for the failed logins that would spend it in
        production. A submission from the same IP must still be accepted, which
        is only true because it never shared that pool.

        The throttle is patched onto the view rather than set via
        @override_settings because DRF binds DEFAULT_THROTTLE_CLASSES onto
        APIView at import time, so overriding the setting afterwards does not
        reach an already-imported view. Patching the class attribute does, since
        as_view() instantiates the class per request.
        """
        class _TinyAnonCeiling(ClientIPAnonRateThrottle):
            # rate set directly, so it never consults DEFAULT_THROTTLE_RATES
            # (which the test runner nulls to switch throttling off).
            rate = "3/day"

        login_url = reverse("token_obtain_pair")
        meta = {"REMOTE_ADDR": "10.0.0.5", "HTTP_X_FORWARDED_FOR": "41.2.3.4, 10.0.0.5"}
        creds = {"email": "x@example.com", "password": "wrong"}

        with patch.object(MyTokenObtainPairView, "throttle_classes", [_TinyAnonCeiling]):
            for attempt in range(3):
                resp = self.client.post(login_url, creds, format="json", **meta)
                self.assertNotEqual(resp.status_code, 429, f"login {attempt + 1}")

            # The shared ceiling is now spent for this IP.
            spent = self.client.post(login_url, creds, format="json", **meta)
            self.assertEqual(spent.status_code, 429)
            # And it is distinguishable from a per-endpoint limit, which is what
            # lets a frontend tell "you are limited" from "this IP is".
            self.assertEqual(spent.json()["code"], "throttled_global")

            # …while the lead still gets through.
            lead = self.client.post(SUBMIT_URL, payload(), format="json", **meta)

        self.assertEqual(lead.status_code, 201)
        self.assertEqual(InquiryForm.objects.count(), 1)


class InquiryDedupeWindowFitsTheBurstTierTests(InquiryAPITestCase):
    """
    The dedupe window and the burst tier have to agree, or a lead is lost.

    The window swallows an identical resubmit silently; the burst tier counts it.
    So the window must fit INSIDE the burst window with at least one attempt to
    spare — otherwise a lead who double-clicks and then notices a mistake has no
    attempt left to correct it, and the correction is simply lost. Under the old
    3/h this failed: the correction was the third attempt and the hour had not
    rolled.

    A property test rather than a request test — it is the configuration that has
    to hold, whatever the numbers are set to.
    """

    def test_the_burst_window_contains_the_dedupe_window(self):
        _, burst_seconds = _split_rate(settings.RATE_LIMITS["inquiry_submit_burst"])
        self.assertGreaterEqual(
            burst_seconds, services.DEDUPE_WINDOW_SECONDS,
            "the dedupe window outlives the burst window: a submission can be "
            "silently swallowed in a window the lead has no attempt left in",
        )

    def test_the_burst_tier_leaves_room_for_a_correction(self):
        """>= 2 attempts, so a double-click (which costs two) still leaves one."""
        burst_count, _ = _split_rate(settings.RATE_LIMITS["inquiry_submit_burst"])
        self.assertGreaterEqual(burst_count, 2)

    def test_the_flood_tier_is_looser_than_the_burst_tier(self):
        """The per-IP tier is a backstop, not the thing a real person meets. If
        it were tighter than the burst tier it would be doing the burst tier's
        job with none of its per-lead fairness."""
        burst_count, _ = _split_rate(settings.RATE_LIMITS["inquiry_submit_burst"])
        flood_count, _ = _split_rate(settings.RATE_LIMITS["inquiry_submit_ip"])
        self.assertGreater(flood_count, burst_count)


class InquiryCsrfTests(InquiryAPITestCase):
    """
    The public POST must survive CsrfViewMiddleware.

    Every other test in this file uses the default client, which SKIPS CSRF
    entirely — so the whole suite passed green while a real browser/curl POST got
    `403 (CSRF cookie not set.)`. Found by hand, not by the suite; these two tests
    are the guard so it can't come back.

    The trap is specific to this route: CsrfViewMiddleware reads `csrf_exempt` off
    the callback URL resolution returns, and /inquiries/ resolves to the plain
    method dispatcher in urls.py, not to a DRF `api_view` result (which sets that
    attribute on itself). Moving the decorator onto submit_inquiry does NOT fix it
    — the middleware never sees that function.
    """

    def test_public_submit_succeeds_without_a_csrf_token(self):
        # enforce_csrf_checks=True is the whole point: this is the only client in
        # the file that behaves like a real HTTP caller.
        strict = APIClient(enforce_csrf_checks=True)

        response = strict.post(SUBMIT_URL, payload(), format="json")

        self.assertEqual(response.status_code, 201, response.content[:200])
        self.assertEqual(response.json()["detail"], ACK_DETAIL)
        self.assertEqual(InquiryForm.objects.count(), 1)

    def test_the_exemption_sits_on_the_callback_the_middleware_reads(self):
        # Asserts the mechanism, not just the outcome: if someone relocates
        # csrf_exempt to the view, the test above could still pass for the wrong
        # reason on some future DRF version. This pins WHERE it has to be.
        from django.urls import resolve

        callback = resolve(SUBMIT_URL).func
        self.assertEqual(callback.__name__, "inquiries_collection")
        self.assertTrue(getattr(callback, "csrf_exempt", False))


@override_settings(RATELIMIT_ENABLE=True)
class InquiryDedupeDoesNotCostAnAttemptTests(InquiryAPITestCase):
    """
    A double-click must not spend two of a lead's attempts (R5, finally closed).

    The limiter has to be applied outermost at the URL, so it increments before
    the view runs; the dedupe window sits inside the service and collapses the
    two clicks into one lead and one pair of emails. The request that got thrown
    away used to count exactly as much as the one that was kept.

    The burst tier's rate is now a callable (``dedupe.burst_rate``) that returns
    None for a submission already accepted inside the dedupe window, and None
    makes django-ratelimit skip the check without incrementing.
    """

    BURST_COUNT, _ = _split_rate(settings.RATE_LIMITS["inquiry_submit_burst"])
    FLOOD_COUNT, _ = _split_rate(settings.RATE_LIMITS["inquiry_submit_ip"])

    def setUp(self):
        super().setUp()
        patcher = patch("django_ratelimit.core.time.time", return_value=1_700_000_000.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, body, client_ip="41.2.3.4"):
        return self.client.post(
            SUBMIT_URL, body, format="json",
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_FOR=f"{client_ip}, 10.0.0.5",
        )

    def test_repeating_an_identical_payload_never_exhausts_the_burst_tier(self):
        """The headline behaviour. Well past the burst count, all identical, and
        none of them are refused — because after the first, every one is a
        submission the dedupe window has already accepted."""
        for attempt in range(self.BURST_COUNT + 4):
            response = self._post(payload())
            self.assertEqual(response.status_code, 201, f"click {attempt + 1}")

        # Still exactly one lead and one client acknowledgement.
        self.assertEqual(InquiryForm.objects.count(), 1)

    def test_a_double_click_leaves_the_full_allowance_for_real_submissions(self):
        """What R5 actually cost: the attempts a lead had left AFTER fumbling.

        Double-click, then submit the burst allowance worth of genuinely
        different leads. Before the fix the double-click had already spent one of
        them.
        """
        self._post(payload())
        self._post(payload())  # the duplicate — must be free

        for attempt in range(self.BURST_COUNT):
            response = self._post(payload(email=f"real{attempt}@example.com"))
            self.assertEqual(response.status_code, 201, f"distinct lead {attempt + 1}")

    def test_a_changed_field_is_a_new_submission_and_does_count(self):
        """The skip is only for a byte-identical repeat. A corrected resubmit is a
        different submission and must be counted — otherwise the burst tier could
        be walked indefinitely by editing one character."""
        for attempt in range(self.BURST_COUNT):
            response = self._post(payload(details=f"Correction {attempt}"))
            self.assertEqual(response.status_code, 201, f"correction {attempt + 1}")

        blocked = self._post(payload(details="One correction too many"))
        self.assertEqual(blocked.status_code, 429)

    def test_a_replayed_payload_still_spends_the_per_ip_flood_allowance(self):  # noqa: E501
        """The skip applies to the burst tier ONLY.

        Otherwise an identical payload could be replayed forever at zero cost.
        Each replay writes no row and sends no email, but it is still a request,
        and the flood tier counts every one — so the free pass is bounded.
        """
        for attempt in range(self.FLOOD_COUNT):
            response = self._post(payload())
            self.assertEqual(response.status_code, 201, f"replay {attempt + 1}")

        blocked = self._post(payload())
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["code"], "rate_limited")
        self.assertEqual(InquiryForm.objects.count(), 1)

    def test_a_failed_submission_is_not_marked_as_accepted(self):
        """A 500 must not buy a free pass.

        The marker is written after create_inquiry returns, never in a finally:
        if the submission blew up the lead was NOT captured, and marking it would
        let the retry skip the limiter while the lead is still lost. Here the
        retry is expected to be counted like any first attempt.
        """
        with patch("apps.inquiries.views.services.create_inquiry",
                   side_effect=RuntimeError("boom")):
            # custom_exception_handler turns an unhandled exception into the
            # standard 500 envelope, so nothing propagates to the caller here.
            failed = self._post(payload())
        self.assertEqual(failed.status_code, 500)

        # The failed attempt left no marker, so it was COUNTED like any first
        # attempt — which is the point. Proof: only BURST_COUNT - 1 attempts
        # remain, not the full allowance.
        for attempt in range(self.BURST_COUNT - 1):
            self.assertEqual(
                self._post(payload(details=f"retry {attempt}")).status_code, 201,
                f"retry {attempt + 1}",
            )
        self.assertEqual(self._post(payload(details="over")).status_code, 429)

    def test_a_non_json_body_falls_back_to_counting(self):
        """Every uncertainty resolves to "count it". A body the fingerprint can't
        read is treated exactly as it was before this mechanism existed."""
        for _ in range(self.BURST_COUNT):
            self.client.post(
                SUBMIT_URL, payload(), format="multipart",
                REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="41.2.3.4, 10.0.0.5",
            )
        blocked = self.client.post(
            SUBMIT_URL, payload(), format="multipart",
            REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="41.2.3.4, 10.0.0.5",
        )
        self.assertEqual(blocked.status_code, 429)


class InquiryDedupeFingerprintTests(TestCase):
    """The raw-body fingerprint, in isolation."""

    def _request(self, body):
        return RequestFactory().post(
            "/api/v1/inquiries/", data=json.dumps(body),
            content_type="application/json",
        )

    def test_identical_bodies_share_a_fingerprint(self):
        body = payload()
        self.assertEqual(
            dedupe.fingerprint_from_request_body(self._request(body)),
            dedupe.fingerprint_from_request_body(self._request(dict(body))),
        )

    def test_key_order_does_not_change_the_fingerprint(self):
        body = payload()
        reordered = dict(reversed(list(body.items())))
        self.assertEqual(
            dedupe.fingerprint_from_request_body(self._request(body)),
            dedupe.fingerprint_from_request_body(self._request(reordered)),
        )

    def test_a_fresh_captcha_token_is_not_a_different_submission(self):
        """recaptcha_token is a single-use credential, not part of the lead's
        identity — a retry may legitimately carry a new one."""
        self.assertEqual(
            dedupe.fingerprint_from_request_body(self._request(payload(recaptcha_token="aaa"))),
            dedupe.fingerprint_from_request_body(self._request(payload(recaptcha_token="bbb"))),
        )

    def test_any_other_change_is_a_different_submission(self):
        base = dedupe.fingerprint_from_request_body(self._request(payload()))
        for field, value in [("details", "changed"), ("email", "other@example.com"),
                             ("budget", "999.00"), ("desired_location", "Abuja")]:
            self.assertNotEqual(
                dedupe.fingerprint_from_request_body(self._request(payload(**{field: value}))),
                base, field,
            )

    def test_an_unusable_body_returns_none(self):
        """None is the "I don't know" answer, and every caller treats it as
        "count this request normally"."""
        garbage = RequestFactory().post(
            "/api/v1/inquiries/", data=b"not json at all",
            content_type="application/json",
        )
        self.assertIsNone(dedupe.fingerprint_from_request_body(garbage))
        self.assertIsNone(dedupe.fingerprint_from_request_body(self._request({})))

    def test_the_marker_shares_the_dedupe_window(self):
        """The marker and the dedupe claim are one decision — "this is the
        submission we already have" — so they must not be able to drift apart."""
        cache.clear()
        self.addCleanup(cache.clear)

        # No marker yet: the burst tier gets its normal configured rate, so the
        # request is counted exactly as before this mechanism existed.
        self.assertEqual(
            dedupe.burst_rate("g", self._request(payload())),
            settings.RATE_LIMITS["inquiry_submit_burst"],
        )

        # Once accepted, an identical submission is not counted at all — None is
        # what makes django-ratelimit skip the check without incrementing.
        dedupe.mark_submission_accepted(payload())
        self.assertIsNone(dedupe.burst_rate("g", self._request(payload())))

        # A different submission is unaffected by that marker.
        self.assertEqual(
            dedupe.burst_rate("g", self._request(payload(details="different"))),
            settings.RATE_LIMITS["inquiry_submit_burst"],
        )


class InquiryStatusTransitionTests(InquiryAPITestCase):
    """
    The triage state machine (INQUIRY_V2_BACKLOG.md §2).

    Two failures that a caller must be able to tell apart:
      * a value that is not a status at all -> VALIDATION_ERROR
      * a real status the lead may not move to -> INVALID_TRANSITION
    """

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            email="triage@example.com", password="pw12345678",
            first_name="Tola", last_name="Ade", role="staff",
        )

    def _lead(self, status=InquiryForm.Status.NEW) -> InquiryForm:
        start = timezone.localdate() + datetime.timedelta(days=90)
        return InquiryForm.objects.create(
            first_name="Ada", last_name="Obi", email="ada@example.com",
            phone_number="+2348010000009", contact_mode="Email", event_type="Wedding",
            preferred_start_date=start, preferred_end_date=start + datetime.timedelta(days=1),
            desired_location="Lagos", details="Garden ceremony.", status=status,
        )

    def _patch(self, lead, new_status):
        client = APIClient()
        client.force_authenticate(user=self.staff)
        return client.patch(
            reverse("update_inquiry_status", args=[lead.id]),
            {"status": new_status}, format="json",
        )

    # ── the table itself ────────────────────────────────────────────────

    def test_every_status_is_a_key_in_the_table(self):
        """Guard on the guard: a new Status member without a row would fall to
        `VALID_TRANSITIONS.get(..., [])` and become silently terminal."""
        self.assertEqual(
            set(services.VALID_TRANSITIONS), set(InquiryForm.Status.values)
        )

    def test_no_status_lists_itself(self):
        """Same-status is handled as an idempotent no-op in the service, not as
        a table entry — the table stays a statement about real movement."""
        for current, allowed in services.VALID_TRANSITIONS.items():
            self.assertNotIn(current, allowed, current)

    def test_every_target_is_a_real_status(self):
        for current, allowed in services.VALID_TRANSITIONS.items():
            for target in allowed:
                self.assertIn(target, InquiryForm.Status.values, f"{current}->{target}")

    # ── legal moves ─────────────────────────────────────────────────────

    def test_the_ordinary_pipeline_walks_end_to_end(self):
        lead = self._lead()
        for nxt in ["contacted", "qualified", "converted", "archived"]:
            self.assertEqual(self._patch(lead, nxt).status_code, 200, nxt)
            lead.refresh_from_db()
            self.assertEqual(lead.status, nxt)

    def test_a_lead_may_skip_ahead(self):
        """new -> qualified without an invented stop at contacted. The guard
        rejects nonsense, it does not enforce a script."""
        lead = self._lead()
        self.assertEqual(self._patch(lead, "qualified").status_code, 200)

    def test_a_lost_lead_can_be_revived(self):
        """'They came back six months later' is a real workflow. Made terminal,
        staff would create a duplicate lead instead — which is worse."""
        lead = self._lead(InquiryForm.Status.LOST)
        self.assertEqual(self._patch(lead, "contacted").status_code, 200)

    def test_an_archived_lead_can_be_restored(self):
        """One exit, so a mis-click is not permanent."""
        lead = self._lead(InquiryForm.Status.ARCHIVED)
        self.assertEqual(self._patch(lead, "new").status_code, 200)

    # ── illegal moves ───────────────────────────────────────────────────

    def test_converted_is_near_terminal(self):
        """Conversion creates a user account and an event; reversing it would
        orphan both. Archive is the only exit."""
        for blocked in ["new", "contacted", "qualified", "lost"]:
            lead = self._lead(InquiryForm.Status.CONVERTED)
            response = self._patch(lead, blocked)
            self.assertEqual(response.status_code, 400, blocked)
            self.assertEqual(response.json()["code"], "invalid_transition", blocked)
            lead.refresh_from_db()
            self.assertEqual(lead.status, InquiryForm.Status.CONVERTED)

        lead = self._lead(InquiryForm.Status.CONVERTED)
        self.assertEqual(self._patch(lead, "archived").status_code, 200)

    def test_an_archived_lead_cannot_jump_straight_back_into_the_pipeline(self):
        lead = self._lead(InquiryForm.Status.ARCHIVED)
        response = self._patch(lead, "converted")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_transition")

    def test_an_illegal_move_names_both_ends(self):
        lead = self._lead(InquiryForm.Status.CONVERTED)
        detail = self._patch(lead, "new").json()["detail"]
        self.assertIn("converted", detail)
        self.assertIn("new", detail)

    def test_a_bad_value_is_a_validation_error_not_a_transition_error(self):
        """The value check runs FIRST, so a typo never reports itself as an
        illegal transition — two different failures, two codes."""
        lead = self._lead()
        response = self._patch(lead, "lost_forever")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "validation_error")

    # ── the same-status no-op ───────────────────────────────────────────

    def test_resending_the_current_status_is_an_accepted_no_op(self):
        """A frontend double-click asks for the state the row is already in.
        Deliberately diverges from meetings, where a status never lists itself."""
        lead = self._lead(InquiryForm.Status.CONTACTED)
        response = self._patch(lead, "contacted")
        self.assertEqual(response.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.status, InquiryForm.Status.CONTACTED)

    def test_the_no_op_still_refreshes_attribution(self):
        """It writes, so 'someone looked at this and confirmed it' is recorded
        rather than leaving the response's attribution stale."""
        lead = self._lead(InquiryForm.Status.CONTACTED)
        self.assertIsNone(lead.last_updated_by)
        self._patch(lead, "contacted")
        lead.refresh_from_db()
        self.assertEqual(lead.last_updated_by, self.staff)

    # ── attribution ─────────────────────────────────────────────────────

    def test_the_acting_staff_member_is_recorded(self):
        lead = self._lead()
        self.assertIsNone(lead.last_updated_by)
        self._patch(lead, "contacted")
        lead.refresh_from_db()
        self.assertEqual(lead.last_updated_by, self.staff)
        self.assertIsNone(lead.created_by)

    def test_attribution_follows_a_rename(self):
        """Resolved from the FK at read time, so it is never a frozen string —
        the whole reason the backlog specified user_display_name()."""
        lead = self._lead()
        self._patch(lead, "contacted")

        self.staff.last_name = "Adeyemi"
        self.staff.save(update_fields=["last_name"])

        lead.refresh_from_db()
        self.assertEqual(user_display_name(lead.last_updated_by), "Tola Adeyemi")

    def test_a_blocked_move_records_nothing(self):
        """The guard raises before the save, so a refused request leaves no
        attribution behind."""
        lead = self._lead(InquiryForm.Status.CONVERTED)
        self._patch(lead, "new")
        lead.refresh_from_db()
        self.assertIsNone(lead.last_updated_by)

    def test_deleting_the_staff_account_keeps_the_lead(self):
        """SET_NULL — losing a staff account must never cascade away leads."""
        lead = self._lead()
        self._patch(lead, "contacted")
        self.staff.delete()

        lead.refresh_from_db()
        self.assertEqual(lead.status, InquiryForm.Status.CONTACTED)
        self.assertIsNone(lead.last_updated_by)
        self.assertEqual(user_display_name(lead.last_updated_by), "")


class InquirySummaryTests(InquiryAPITestCase):
    """
    GET /inquiries/summary/ — per-status pipeline counts (backlog §4).

    Shape mirrors GET /event/<slug>/contacts/summary/: a flat list of
    {value, value_display, count}, one entry per choice.
    """

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            email="dash@example.com", password="pw12345678",
            first_name="Dami", last_name="Oke", role="staff",
        )
        self.client_user = User.objects.create_user(
            email="client@example.com", password="pw12345678",
            first_name="Cee", last_name="Lient", role="client",
        )
        self.url = reverse("inquiry_summary")

    def _lead(self, *, status=InquiryForm.Status.NEW, event_type="Wedding",
              first_name="Ada", location="Lagos"):
        start = timezone.localdate() + datetime.timedelta(days=60)
        return InquiryForm.objects.create(
            first_name=first_name, last_name="Obi", email="ada@example.com",
            phone_number="+2348010000009", contact_mode="Email", event_type=event_type,
            preferred_start_date=start, preferred_end_date=start + datetime.timedelta(days=1),
            desired_location=location, details="x", status=status,
        )

    def _get(self, **params):
        client = APIClient()
        client.force_authenticate(user=self.staff)
        return client.get(self.url, params)

    def _counts(self, body) -> dict:
        return {row["status"]: row["count"] for row in body}

    # ── shape ───────────────────────────────────────────────────────────

    def test_every_status_appears_even_at_zero(self):
        """A dashboard renders a fixed set of pipeline columns; omitting empty
        statuses would make them appear and disappear as leads move."""
        response = self._get()
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual([row["status"] for row in body], list(InquiryForm.Status.values))
        self.assertTrue(all(row["count"] == 0 for row in body))

    def test_each_row_mirrors_the_contacts_summary_shape(self):
        body = self._get().json()
        for row in body:
            self.assertEqual(set(row), {"status", "status_display", "count"})
        self.assertEqual(body[0]["status_display"], "New")

    def test_counts_are_per_status(self):
        self._lead(status=InquiryForm.Status.NEW)
        self._lead(status=InquiryForm.Status.NEW)
        self._lead(status=InquiryForm.Status.CONTACTED)
        self._lead(status=InquiryForm.Status.LOST)

        counts = self._counts(self._get().json())
        self.assertEqual(counts["new"], 2)
        self.assertEqual(counts["contacted"], 1)
        self.assertEqual(counts["lost"], 1)
        self.assertEqual(counts["qualified"], 0)
        self.assertEqual(counts["converted"], 0)
        self.assertEqual(counts["archived"], 0)

    # ── the aggregate ───────────────────────────────────────────────────

    def test_the_summary_is_one_query_regardless_of_lead_count(self):
        """The endpoint exists to avoid work, so it must not be one COUNT per
        status the way the three older summary endpoints are.

        The count assertion beside it is what pins the GROUP BY: three `new`
        leads with three different created_at values must report new=3. If an
        ordering field ever leaked into the grouping they would report new=1.
        """
        for _ in range(3):
            self._lead(status=InquiryForm.Status.NEW)

        client = APIClient()
        client.force_authenticate(user=self.staff)
        with self.assertNumQueries(1):
            body = client.get(self.url).json()

        self.assertEqual(self._counts(body)["new"], 3)

    # ── filters ─────────────────────────────────────────────────────────

    def test_event_type_filter_narrows_the_tally(self):
        self._lead(status=InquiryForm.Status.NEW, event_type="Wedding")
        self._lead(status=InquiryForm.Status.NEW, event_type="Birthday")

        counts = self._counts(self._get(event_type="Wedding").json())
        self.assertEqual(counts["new"], 1)

    def test_search_filter_narrows_the_tally(self):
        self._lead(status=InquiryForm.Status.NEW, first_name="Ada")
        self._lead(status=InquiryForm.Status.NEW, first_name="Chidi")

        counts = self._counts(self._get(search="chidi").json())
        self.assertEqual(counts["new"], 1)

    def test_the_status_filter_is_ignored(self):
        """Filtering a PER-STATUS tally by status would leave one populated row
        and five zeros — it answers nothing the caller didn't already know."""
        self._lead(status=InquiryForm.Status.NEW)
        self._lead(status=InquiryForm.Status.CONTACTED)

        counts = self._counts(self._get(status="new").json())
        self.assertEqual(counts["new"], 1)
        self.assertEqual(counts["contacted"], 1)

    def test_the_tally_agrees_with_the_list_under_the_same_filter(self):
        """The two share _filtered_inquiries precisely so they cannot drift."""
        self._lead(status=InquiryForm.Status.NEW, first_name="Ada")
        self._lead(status=InquiryForm.Status.CONTACTED, first_name="Ada")
        self._lead(status=InquiryForm.Status.NEW, first_name="Chidi")

        client = APIClient()
        client.force_authenticate(user=self.staff)
        listed = client.get(SUBMIT_URL, {"search": "ada"}).json()["count"]
        tallied = sum(row["count"] for row in client.get(self.url, {"search": "ada"}).json())
        self.assertEqual(listed, tallied)

    # ── access ──────────────────────────────────────────────────────────

    def test_anonymous_is_401(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_a_client_account_is_403(self):
        """Counts are still business intelligence — same gate as the lead list."""
        client = APIClient()
        client.force_authenticate(user=self.client_user)
        self.assertEqual(client.get(self.url).status_code, 403)

    def test_summary_is_not_swallowed_by_the_detail_route(self):
        """<uuid:inquiry_id> cannot match "summary", so this resolves to its own
        view rather than 404ing as an unknown lead."""
        self.assertEqual(resolve(self.url).func, views.inquiry_summary)
