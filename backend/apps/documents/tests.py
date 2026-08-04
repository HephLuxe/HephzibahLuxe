"""
apps/documents/tests.py

The generic-FK registry (docs/FAILURE_POINTS_AUDIT.md F4): source models have
mixed PK types (Event/PrepItemFileUpload int; EventDay/EventContact/TeamMember
UUID), so object_id is a CharField. These lock down that a UUID-PK source
round-trips through the GenericForeignKey, and that register/unregister behave.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.contacts.models import EventContact
from apps.documents.models import Document, DocumentCategory
from apps.documents.services import register_document, unregister_document
from apps.events.models import Event, EventDay

User = get_user_model()


class DocumentRegistryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="d-client@example.com", password="x",
        )
        self.event = Event.objects.create(
            celebrant=self.user, title="Doc Event", event_type="Wedding",
            groom_name="Sam", bride_name="Pris", country="NG", state="Lagos",
            event_date=datetime.date(2027, 6, 1),
        )
        self.day = EventDay.objects.create(owner=self.event, date=datetime.date(2027, 6, 1))
        # EventContact has a UUID PK — the case F4 was about.
        self.contact = EventContact.objects.create(
            event=self.event, event_day=self.day, category="primary", name="Jo",
        )

    def test_register_document_round_trips_a_uuid_pk_source(self):
        doc, created = register_document(
            engagement=None, source_instance=self.contact,
            file_path="portals/x/contacts/1/photo.jpg",
            category=DocumentCategory.CONTACT_PHOTO,
        )
        self.assertTrue(created)
        # Reload fresh: the CharField stores/returns the UUID as a string, and
        # the GenericForeignKey must resolve back to the original contact.
        fresh = Document.objects.get(pk=doc.pk)
        self.assertEqual(fresh.object_id, str(self.contact.pk))
        self.assertEqual(fresh.source, self.contact)

    def test_register_is_an_idempotent_update_not_a_duplicate(self):
        register_document(
            engagement=None, source_instance=self.contact,
            file_path="a.jpg", category=DocumentCategory.CONTACT_PHOTO,
        )
        doc, created = register_document(
            engagement=None, source_instance=self.contact,
            file_path="b.jpg", category=DocumentCategory.CONTACT_PHOTO,
        )
        self.assertFalse(created)
        self.assertEqual(doc.file_path, "b.jpg")
        self.assertEqual(
            Document.objects.filter(object_id=str(self.contact.pk)).count(), 1
        )

    def test_unregister_removes_the_registry_row(self):
        register_document(
            engagement=None, source_instance=self.contact,
            file_path="a.jpg", category=DocumentCategory.CONTACT_PHOTO,
        )
        unregister_document(self.contact)
        self.assertFalse(
            Document.objects.filter(object_id=str(self.contact.pk)).exists()
        )
