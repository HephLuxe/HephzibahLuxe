"""
apps/portal/test_activate_event.py

PATCH /portal/activate-event/ may only bind a portal to an event its own client
owns, and an event already engaged on a different portal is a clean 409, not
the IntegrityError 500 the one-to-one Event.engagement used to produce.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.events.models import Event
from apps.portal.models import ClientPortal, EventEngagement

User = get_user_model()


def _event(celebrant, title):
    return Event.objects.create(
        celebrant=celebrant, title=title, event_type="Birthday", honoree_name="X",
        country="NG", state="Lagos", event_date=datetime.date(2027, 6, 1),
    )


class ActivateEventOwnershipTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.staff = User.objects.create_user(
            first_name="St", last_name="Aff", email="act-staff@example.com", password="x", role="staff",
        )
        self.ada = User.objects.create_user(first_name="Ada", last_name="O", email="act-ada@example.com", password="x")
        self.ben = User.objects.create_user(first_name="Ben", last_name="O", email="act-ben@example.com", password="x")
        self.ada_portal = ClientPortal.objects.get(user=self.ada)
        self.ben_portal = ClientPortal.objects.get(user=self.ben)
        self.api.force_authenticate(self.staff)

    def _activate(self, portal, event):
        return self.api.patch(
            reverse("portal-activate-event"),
            {"portal_id": str(portal.id), "event_slug": event.slug}, format="json",
        )

    def test_another_clients_event_is_refused(self):
        bens_event = _event(self.ben, "Ben Event")

        resp = self._activate(self.ada_portal, bens_event)

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["code"], "validation_error")
        self.assertFalse(EventEngagement.objects.filter(portal=self.ada_portal).exists())

    def test_an_event_engaged_on_another_portal_is_409_not_500(self):
        event = _event(self.ada, "Ada Event")
        EventEngagement.objects.create(portal=self.ben_portal, event=event, is_active=True)

        resp = self._activate(self.ada_portal, event)

        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertEqual(resp.data["code"], "validation_error")
        self.assertEqual(EventEngagement.objects.get(event=event).portal, self.ben_portal)

    def test_the_owners_event_activates(self):
        event = _event(self.ada, "Ada Event")

        resp = self._activate(self.ada_portal, event)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(EventEngagement.objects.get(event=event, portal=self.ada_portal).is_active)
