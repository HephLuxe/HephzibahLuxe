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
    def test_request_context_absolutizes_the_photo_url(self):
        # Pin to in-memory storage so this is deterministic even if the .env has
        # R2 on (with R2 the URL is absolute regardless).
        field = EventContact._meta.get_field("photo")
        with mock.patch.object(field, "storage", InMemoryStorage()):
            self.contact.photo = "portals/test/contacts/1/photo.jpg"
            without_ctx = EventContactSerializer(self.contact).data["photo"]
            with_ctx = EventContactSerializer(
                self.contact, context={"request": factory.get("/")}
            ).data["photo"]
        self.assertTrue(without_ctx.startswith("/media/"))
        self.assertTrue(with_ctx.startswith("http"))
