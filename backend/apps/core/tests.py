import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import serializers

from apps.core.serializers import AttributionSerializerMixin
from apps.core.utils import save_with_attribution, stamp_attribution, user_display_name
from apps.events.models import Event
from apps.events.serializers import EventSerializer


class HealthEndpointTests(TestCase):
    """The observability standard's probes. /health/ must be a bare,
    unauthenticated 200 (the home-server control panel depends on it)."""

    def test_health_live_is_unauthenticated_200(self):
        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_health_ready_ok_when_db_reachable(self):
        resp = self.client.get("/health/ready/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")


class RequestIdMiddlewareTests(TestCase):
    def test_response_carries_request_id_header(self):
        resp = self.client.get("/health/")
        self.assertTrue(resp.headers.get("X-Request-ID"))

    def test_inbound_request_id_is_echoed_back(self):
        resp = self.client.get("/health/", headers={"x-request-id": "abc123"})
        self.assertEqual(resp.headers.get("X-Request-ID"), "abc123")


class AttributionTests(TestCase):
    """The shared created_by/last_updated_by attribution layer (AttributedModel,
    the stamping helpers, and AttributionSerializerMixin), exercised through
    Event as a representative attributed model."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create_user(
            first_name="Winnie", last_name="Adeyemi", email="attr-staff@example.com",
            password="x", role="staff",
        )
        cls.other = User.objects.create_user(
            first_name="Tosin", last_name="Bello", email="attr-other@example.com",
            password="x", role="staff",
        )
        cls.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="attr-client@example.com", password="x",
        )

    def _event(self, **extra):
        return Event.objects.create(
            celebrant=self.client_user, title="P & S", event_type="Wedding",
            bride_name="Priscilla", groom_name="Samuel", country="NG", state="Lagos",
            event_date=datetime.date(2027, 1, 1), **extra,
        )

    def test_save_with_attribution_stamps_both_on_create(self):
        serializer = EventSerializer(data={
            "title": "x", "country": "NG", "state": "Lagos",
            "event_date": "2027-01-01", "event_type": "Wedding",
            "bride_name": "P", "groom_name": "S",
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        event = save_with_attribution(serializer, self.staff, celebrant=self.client_user)
        self.assertEqual(event.created_by, self.staff)
        self.assertEqual(event.last_updated_by, self.staff)

    def test_update_changes_last_updated_by_but_not_created_by(self):
        event = self._event(created_by=self.staff, last_updated_by=self.staff)
        serializer = EventSerializer(event, data={"description": "edited"}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        save_with_attribution(serializer, self.other)
        event.refresh_from_db()
        self.assertEqual(event.created_by, self.staff)        # unchanged
        self.assertEqual(event.last_updated_by, self.other)   # now the editor

    def test_serializer_exposes_names_and_never_raw_ids(self):
        event = self._event(created_by=self.staff, last_updated_by=self.other)
        data = EventSerializer(event).data
        self.assertEqual(data["created_by_display"], "Winnie Adeyemi")
        self.assertEqual(data["last_updated_by_display"], "Tosin Bello")
        # The raw FK ids must never leak (the `last_updated_by: 1` bug).
        self.assertNotIn("created_by", data)
        self.assertNotIn("last_updated_by", data)

    def test_display_is_blank_when_unset(self):
        data = EventSerializer(self._event()).data
        self.assertEqual(data["created_by_display"], "")
        self.assertEqual(data["last_updated_by_display"], "")

    def test_stamp_attribution_ignores_anonymous(self):
        from django.contrib.auth.models import AnonymousUser
        event = self._event()
        stamp_attribution(event, AnonymousUser())
        self.assertIsNone(event.last_updated_by_id)

    def test_display_falls_back_to_email_without_a_name(self):
        User = get_user_model()
        nameless = User(first_name="", last_name="", email="ops@example.com")
        self.assertEqual(user_display_name(nameless), "ops@example.com")
