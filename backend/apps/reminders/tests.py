"""
apps/reminders/tests.py

Behavioural tests for the reminders app. Run with:
    python manage.py test reminders

Uses Django's TestCase (each test runs in a transaction that is rolled back),
so no data persists. Mirrors the verified end-to-end flow: staff create,
client list/sort, client complete, and cross-tenant isolation.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.events.models import Event
from apps.portal.models import ClientPortal, EventEngagement

from . import views

User = get_user_model()


class ReminderTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.client_user = User.objects.create_user(
            first_name="Test", last_name="Client", email="client@example.com", password="x"
        )
        self.staff = User.objects.create_user(
            first_name="Test", last_name="Staff", email="staff@example.com", password="x", role="staff"
        )
        self.other = User.objects.create_user(
            first_name="Other", last_name="Client", email="other@example.com", password="x"
        )
        # ClientPortal is auto-created by the portal signal on client creation.
        self.portal = ClientPortal.objects.get(user=self.client_user)
        self.event = Event.objects.create(
            celebrant=self.client_user, title="Smoke Wedding", country="NG",
            state="Lagos", event_date=datetime.date(2026, 11, 27),
        )
        self.engagement = EventEngagement.objects.create(
            portal=self.portal, event=self.event, is_active=True, current_phase="connect"
        )

    def _create(self, **body):
        req = self.factory.post("/api/v1/reminders/create/", body, format="json")
        force_authenticate(req, user=self.staff)
        return views.create_reminder(req)

    def test_staff_can_create_and_client_cannot(self):
        r = self._create(portal_id=str(self.portal.id), title="Kickoff", priority="high")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["priority"], "high")
        self.assertFalse(r.data["is_completed"])

        # A client hitting the staff-only create is rejected by the permission class.
        req = self.factory.post(
            "/api/v1/reminders/create/",
            {"portal_id": str(self.portal.id), "title": "x"},
            format="json",
        )
        force_authenticate(req, user=self.client_user)
        self.assertEqual(views.create_reminder(req).status_code, 403)

    def test_priority_sort_and_pending_filter(self):
        self._create(portal_id=str(self.portal.id), title="H", priority="high")
        self._create(portal_id=str(self.portal.id), title="M", priority="medium")
        self._create(portal_id=str(self.portal.id), title="L", priority="low")

        req = self.factory.get("/api/v1/reminders/")
        force_authenticate(req, user=self.client_user)
        data = views.list_reminders(req).data
        self.assertEqual([x["priority"] for x in data], ["high", "medium", "low"])

    def test_client_completes_and_it_drops_from_pending(self):
        rid = self._create(portal_id=str(self.portal.id), title="H", priority="high").data["id"]

        req = self.factory.patch(
            f"/api/v1/reminders/{rid}/complete/", {"is_completed": True}, format="json"
        )
        force_authenticate(req, user=self.client_user)
        r = views.complete_reminder(req, reminder_id=rid)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["is_completed"])
        self.assertIsNotNone(r.data["completed_at"])

        req = self.factory.get("/api/v1/reminders/")
        force_authenticate(req, user=self.client_user)
        self.assertEqual(len(views.list_reminders(req).data), 0)

    def test_cross_tenant_isolation(self):
        rid = self._create(portal_id=str(self.portal.id), title="H", priority="high").data["id"]
        req = self.factory.get(f"/api/v1/reminders/{rid}/")
        force_authenticate(req, user=self.other)
        r = views.reminder_detail(req, reminder_id=rid)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data["code"], "permission_denied")

    def test_empty_state_returns_empty_list(self):
        req = self.factory.get("/api/v1/reminders/")
        force_authenticate(req, user=self.client_user)
        self.assertEqual(views.list_reminders(req).data, [])
