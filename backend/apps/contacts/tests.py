"""
apps/contacts/tests.py

Contact photos are one of the "event pictures" served to the frontend, so they
get the same absolute-URL treatment as event covers: the view threads the
request into the serializer context, and the photo field renders an absolute URL
(resolvable from a separate frontend origin under local storage; already
absolute under R2).
"""

import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.storage import InMemoryStorage
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from apps.contacts.models import EventContact
from apps.contacts.serializers import EventContactSerializer
from apps.events.models import Event, EventDay

User = get_user_model()
factory = APIRequestFactory()


class ContactPhotoUrlTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="c-client@example.com", password="x",
        )
        self.event = Event.objects.create(
            celebrant=self.client_user, title="Contacts Event", event_type="Wedding",
            groom_name="Sam", bride_name="Pris", country="NG", state="Lagos",
            event_date=datetime.date(2027, 6, 1),
        )
        self.day = EventDay.objects.create(owner=self.event, date=datetime.date(2027, 6, 1))
        self.contact = EventContact.objects.create(
            event=self.event, event_day=self.day, category="primary", name="Jo Planner",
        )
    def test_the_photo_reads_as_a_mint_path_not_a_storage_url(self):
        """
        Contact photos moved to the private storage tier, so the serializer no
        longer emits a storage URL at all — it emits the endpoint that mints one
        after an ownership check (apps/core/filelinks.py).

        This replaces a test that asserted the request context *absolutized* the
        storage URL. That concern moved with the URL: a minted R2 URL is absolute
        by construction, and the serializer's output is now a fixed path, which
        is the point — it never expires, so the frontend can cache it.
        """
        field = EventContact._meta.get_field("photo")
        with mock.patch.object(field, "storage", InMemoryStorage()):
            self.contact.photo = "portals/test/contacts/1/photo.jpg"
            without_ctx = EventContactSerializer(self.contact).data["photo_url"]
            with_ctx = EventContactSerializer(
                self.contact, context={"request": factory.get("/")}
            ).data["photo_url"]

        expected = f"/api/v1/files/contact-photo/{self.contact.pk}/"
        self.assertEqual(without_ctx, expected)
        # Identical with or without a request: nothing about it is request-derived.
        self.assertEqual(with_ctx, expected)

    def test_a_contact_with_no_photo_reads_as_null(self):
        """So a caller can tell "no photo" from "a photo to go and fetch"."""
        self.assertIsNone(EventContactSerializer(self.contact).data["photo_url"])
