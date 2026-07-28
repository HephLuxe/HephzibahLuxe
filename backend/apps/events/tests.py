"""
apps/events/tests.py

Locks down the behaviours the failure audit (docs/FAILURE_POINTS_AUDIT.md) fixed
by hand, so a future refactor can't silently reopen them:

  * F1 — destructive delete is gated behind an impact preview + ?confirm=true.
  * F2 — clients can never delete an event (staff-only, regardless of locks).
  * F3 (addendum) — EVERY event gets an engagement; the first is active, later
    ones inactive (so a 2nd event can be pre-staged, never "ghosted").

Plus the media-pipeline change: image fields render ABSOLUTE URLs when the view
threads the request into the serializer context (so a separate frontend origin
can resolve them under local storage; with R2 they're absolute regardless).

Views are function-based, so they're driven with APIRequestFactory +
force_authenticate and called directly — the same pattern accounts/tests.py uses.
"""

import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.storage import InMemoryStorage
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.error_codes import CONFIRMATION_REQUIRED
from apps.events import views
from apps.events.models import Event, EventDay
from apps.events.serializers import EventSerializer
from apps.portal.models import ClientPortal

User = get_user_model()
factory = APIRequestFactory()


def _make_event(celebrant, title="Sam & Pris's Wedding"):
    return Event.objects.create(
        celebrant=celebrant, title=title, event_type="Wedding",
        groom_name="Sam", bride_name="Pris", country="NG", state="Lagos",
        event_date=datetime.date(2027, 6, 1),
    )


class EventDeletePermissionTests(TestCase):
    """F1/F2 — who can delete, and the confirmation gate on destructive deletes."""

    def setUp(self):
        self.staff = User.objects.create_user(
            first_name="Win", last_name="Team", email="staff@example.com",
            password="x", role="staff",
        )
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="client@example.com", password="x",
        )
        self.event = _make_event(self.client_user)

    def _delete(self, user, slug, confirm=False):
        req = factory.delete("/?confirm=true" if confirm else "/")
        force_authenticate(req, user=user)
        return views.delete_event(req, slug=slug)

    def test_client_cannot_delete_their_own_event(self):
        # F2: even the event's own celebrant is refused — deletes are staff-only.
        resp = self._delete(self.client_user, self.event.slug)
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_staff_can_delete_an_empty_event_without_confirmation(self):
        resp = self._delete(self.staff, self.event.slug)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())

    def test_delete_with_related_data_requires_confirm(self):
        # F1: a non-empty event refuses to delete without ?confirm=true, and the
        # response carries the impact breakdown so the operator can see the blast.
        EventDay.objects.create(owner=self.event, date=datetime.date(2027, 6, 1))

        blocked = self._delete(self.staff, self.event.slug)
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.data["code"], CONFIRMATION_REQUIRED)
        self.assertGreater(blocked.data["errors"]["impact"]["total"], 0)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

        confirmed = self._delete(self.staff, self.event.slug, confirm=True)
        self.assertEqual(confirmed.status_code, 200)
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())

    def test_delete_impact_preview_counts_related_without_deleting(self):
        EventDay.objects.create(owner=self.event, date=datetime.date(2027, 6, 1))
        req = factory.get("/")
        force_authenticate(req, user=self.staff)
        resp = views.get_event_delete_impact(req, slug=self.event.slug)
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(resp.data["total"], 0)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())  # preview, not delete


class EventEngagementAutoCreateTests(TestCase):
    """F3 addendum — every event gets an engagement; only the first is active."""

    def setUp(self):
        self.staff = User.objects.create_user(
            first_name="Win", last_name="Team", email="staff2@example.com",
            password="x", role="staff",
        )
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="client2@example.com", password="x",
        )
        self.portal = ClientPortal.objects.get(user=self.client_user)

    def _create_event(self):
        req = factory.post("/", {
            "user_email": self.client_user.email, "event_type": "Wedding",
            "groom_name": "Sam", "bride_name": "Pris", "country": "NG",
            "state": "Lagos", "event_date": "2027-06-01",
        }, format="json")
        force_authenticate(req, user=self.staff)
        return views.create_event(req)

    def test_first_event_gets_an_active_engagement(self):
        resp = self._create_event()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self.portal.engagements.count(), 1)
        self.assertEqual(self.portal.engagements.filter(is_active=True).count(), 1)

    def test_second_event_gets_an_inactive_engagement(self):
        self._create_event()
        resp = self._create_event()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self.portal.engagements.count(), 2)
        # Still exactly one active — the 2nd event's engagement is inactive.
        self.assertEqual(self.portal.engagements.filter(is_active=True).count(), 1)


class EventImageUrlTests(TestCase):
    """
    The media-pipeline fix: threading the request into the serializer context
    yields an ABSOLUTE image URL. Pinned to in-memory storage for the duration
    so the assertion is deterministic regardless of whether the .env has R2
    enabled — with R2 the URL is absolute either way; a relative-URL storage is
    exactly the case where the request context is what makes it absolute.
    """

    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="client3@example.com", password="x",
        )
        self.event = _make_event(self.client_user)

    def test_request_context_absolutizes_the_image_url(self):
        field = Event._meta.get_field("featured_image")
        with mock.patch.object(field, "storage", InMemoryStorage()):
            # The FieldFile captures field.storage at assignment — set it here.
            self.event.featured_image = "portals/test/events/1-x/covers/cover.jpg"
            without_ctx = EventSerializer(self.event).data["featured_image"]
            with_ctx = EventSerializer(
                self.event, context={"request": factory.get("/")}
            ).data["featured_image"]
        self.assertTrue(without_ctx.startswith("/media/"))  # relative without a request
        self.assertTrue(with_ctx.startswith("http"))        # absolutized via the request
