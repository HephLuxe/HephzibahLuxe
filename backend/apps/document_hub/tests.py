"""
apps/document_hub/tests.py

Run with: python manage.py test document_hub
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.events.models import Event
from apps.portal.models import ClientPortal, EventEngagement

from . import views
from .models import ClientDocument, ClientDocumentCategory, PaymentSchedule

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
        self.assertIsNone(r.data["service_agreement"])
        self.assertIsNone(r.data["quotation"])
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
        self.assertRegex(r.data["service_agreement"]["reference_code"], r"^HL-PSW\d{3}-C001$")
        self.assertIsNone(r.data["quotation"])

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

    def test_payment_schedule_tiles_and_next_payment_due(self):
        req = self.factory.post(
            "/api/v1/document-hub/payment-schedule/",
            {"portal_id": str(self.portal.id), "total_investment": "7000000"},
            format="json",
        )
        force_authenticate(req, user=self.staff)
        r = views.create_payment_schedule(req)
        self.assertEqual(r.status_code, 201)
        schedule_id = r.data["id"]

        for label, amount, due, status_ in [
            ("Deposit", "3000000", "2026-05-06", "paid"),
            ("Phase 2", "1500000", "2026-06-07", "paid"),
            ("Final Payment", "2500000", "2026-07-06", "pending"),
        ]:
            req = self.factory.post(
                f"/api/v1/document-hub/payment-schedule/{schedule_id}/milestones/",
                {"label": label, "amount": amount, "due_date": due, "status": status_},
                format="json",
            )
            force_authenticate(req, user=self.staff)
            self.assertEqual(views.add_milestone(req, schedule_id=schedule_id).status_code, 201)

        req = self.factory.get("/api/v1/document-hub/")
        force_authenticate(req, user=self.client_user)
        r = views.get_hub(req)
        schedule = r.data["payment_schedule"]
        # paid_to_date/remaining_balance are declared DecimalFields, which DRF
        # coerces to strings by default. next_payment_due_amount comes from a
        # SerializerMethodField (get_next_payment_due_amount), which returns
        # the raw model value with no such coercion — still a Decimal at this
        # point (pre-JSON-render). Both are correct; they're just different
        # representations at the .data (Python) layer, reconciled once DRF's
        # JSON renderer actually serializes the response.
        self.assertEqual(schedule["paid_to_date"], "4500000.00")
        self.assertEqual(schedule["remaining_balance"], "2500000.00")
        self.assertEqual(schedule["next_payment_due_amount"], Decimal("2500000.00"))
        self.assertEqual(str(schedule["next_payment_due_date"]), "2026-07-06")

    def test_mark_milestone_paid(self):
        schedule = PaymentSchedule.objects.create(engagement=self.engagement, total_investment=1000)
        req = self.factory.post(
            f"/api/v1/document-hub/payment-schedule/{schedule.id}/milestones/",
            {"label": "Deposit", "amount": "1000", "due_date": "2026-05-06"},
            format="json",
        )
        force_authenticate(req, user=self.staff)
        milestone_id = views.add_milestone(req, schedule_id=schedule.id).data["id"]

        req = self.factory.patch(f"/api/v1/document-hub/milestones/{milestone_id}/mark-paid/", {}, format="json")
        force_authenticate(req, user=self.staff)
        r = views.mark_milestone_paid(req, milestone_id=milestone_id)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "paid")
        self.assertIsNotNone(r.data["paid_on"])
