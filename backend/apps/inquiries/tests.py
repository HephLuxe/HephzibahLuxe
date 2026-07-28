"""
apps/inquiries/tests.py

InquiryForm is a lead-capture model whose only real rule is the DB-level
CheckConstraint that a preferred end date can't precede the start date.
"""

import datetime

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.inquiries.models import InquiryForm


class InquiryDateRangeConstraintTests(TestCase):
    def test_end_before_start_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            InquiryForm.objects.create(
                first_name="A", last_name="B", email="a@b.com", phone_number="123",
                desired_location="Lagos",
                preferred_start_date=datetime.date(2027, 6, 10),
                preferred_end_date=datetime.date(2027, 6, 1),
            )

    def test_valid_range_is_accepted(self):
        obj = InquiryForm.objects.create(
            first_name="A", last_name="B", email="a@b.com", phone_number="123",
            desired_location="Lagos",
            preferred_start_date=datetime.date(2027, 6, 1),
            preferred_end_date=datetime.date(2027, 6, 10),
        )
        self.assertIsNotNone(obj.pk)
