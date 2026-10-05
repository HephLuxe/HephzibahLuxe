"""
apps/events/test_email_lookup.py

The two by-email list endpoints must not reveal which addresses have accounts:
"no such user" and "a user you may not see" get the identical response, as
UserInfowEmail already does. getall_event_email is paginated like getall_event.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.events.models import Event

User = get_user_model()


class ByEmailListsDoNotEnumerateTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.alice = User.objects.create_user(
            first_name="Al", last_name="Ice", email="enum-alice@example.com", password="x",
        )
        self.bob = User.objects.create_user(
            first_name="Bo", last_name="B", email="enum-bob@example.com", password="x",
        )
        self.staff = User.objects.create_user(
            first_name="St", last_name="Aff", email="enum-staff@example.com", password="x", role="staff",
        )
        Event.objects.create(
            celebrant=self.bob, title="Bob Party", event_type="Birthday", honoree_name="Bob",
            country="NG", state="Lagos", event_date=datetime.date(2027, 6, 1),
        )

    def _get(self, name, email):
        return self.api.get(reverse(name, args=[email]))

    def test_unknown_and_forbidden_addresses_look_identical(self):
        self.api.force_authenticate(self.alice)
        for name in ("getall_event_email", "getall_eventday_email"):
            with self.subTest(name=name):
                missing = self._get(name, "nobody@example.com")
                forbidden = self._get(name, self.bob.email)
                self.assertEqual(missing.status_code, 404)
                self.assertEqual(forbidden.status_code, missing.status_code)
                self.assertEqual(forbidden.json(), missing.json())

    def test_staff_and_self_can_still_read(self):
        for actor in (self.staff, self.bob):
            self.api.force_authenticate(actor)
            self.assertEqual(self._get("getall_event_email", self.bob.email).status_code, 200)
            self.assertEqual(self._get("getall_eventday_email", self.bob.email).status_code, 200)

    def test_event_list_by_email_is_paginated(self):
        self.api.force_authenticate(self.staff)
        body = self._get("getall_event_email", self.bob.email).json()
        self.assertEqual(set(body), {"count", "next", "previous", "results"})
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["title"], "Bob Party")
