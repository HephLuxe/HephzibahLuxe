"""
apps/conversations/tests.py

Tag validation is the one place the JSONField `tags` shape is enforced (it's
not a related model), so a bad tag must be rejected rather than silently stored.
"""

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.conversations.services import validate_tags


class ValidateTagsTests(TestCase):
    def test_valid_tags_pass_through_unchanged(self):
        self.assertEqual(validate_tags(["venue", "budget"]), ["venue", "budget"])

    def test_empty_list_is_allowed(self):
        self.assertEqual(validate_tags([]), [])

    def test_unknown_tag_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_tags(["not_a_real_tag"])
