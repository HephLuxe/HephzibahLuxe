"""
apps/core/test_exception_handler.py

Django's own ValidationError (most often a malformed UUID reaching the ORM via
get_object_or_404 or a filter) used to fall through custom_exception_handler as
a 500. It is bad input, so it now renders as a 400 in the standard envelope.
The file-link mint view keeps its deliberate 404 for a malformed id.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.exceptions import custom_exception_handler

User = get_user_model()


class MalformedIdsAreNotServerErrorsTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.staff = User.objects.create_user(
            first_name="St", last_name="Aff", email="uuid-staff@example.com", password="x", role="staff",
        )
        self.api.force_authenticate(self.staff)

    def test_staff_portal_id_filters_return_400_in_the_envelope(self):
        for name in ("list_meetings", "list_conversations", "list_notifications", "portal-detail"):
            with self.subTest(name=name):
                resp = self.api.get(reverse(name), {"portal_id": "not-a-uuid"})
                self.assertEqual(resp.status_code, 400, resp.content)
                self.assertEqual(resp.data["code"], "validation_error")
                self.assertIn("detail", resp.data)
                self.assertIn("non_field_errors", resp.data["errors"])

    def test_activate_event_with_a_malformed_portal_id_is_400(self):
        resp = self.api.patch(
            reverse("portal-activate-event"), {"portal_id": "nope", "event_slug": "x"}, format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.data["code"], "validation_error")

    def test_file_link_mint_with_a_malformed_id_is_404(self):
        resp = self.api.get(reverse("mint_file_url", args=["client-document", "not-a-uuid"]))
        self.assertEqual(resp.status_code, 404, resp.content)
        self.assertEqual(resp.data["code"], "not_found")


class HandlerShapeTests(TestCase):
    def test_a_field_keyed_error_keeps_its_fields(self):
        resp = custom_exception_handler(DjangoValidationError({"timezone": ["Bad zone."]}), {})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data, {"detail": "Bad zone.", "code": "validation_error", "errors": {"timezone": ["Bad zone."]}},
        )
