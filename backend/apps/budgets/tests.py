"""
apps/budgets/tests.py

The budget model carries the financial logic that drives the Overview tab —
allocated/spent/remaining, the "On Track" vs "Over Budget" badge, and the
payment totals. These are pure computed properties, so they're tested directly
against the ORM. Plus the staff-only permission gate on writes.
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.budgets import views
from apps.budgets.models import BudgetHealthStatus, EventBudget, PaymentStatus
from apps.events.models import Event

User = get_user_model()
factory = APIRequestFactory()


def _make_event(celebrant):
    return Event.objects.create(
        celebrant=celebrant, title="Budget Event", event_type="Wedding",
        groom_name="Sam", bride_name="Pris", country="NG", state="Lagos",
        event_date=datetime.date(2027, 6, 1),
    )


class BudgetTotalsTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="b-client@example.com", password="x",
        )
        self.event = _make_event(self.client_user)
        self.budget = EventBudget.objects.create(event=self.event, total_budget=Decimal("1000"))
        self.budget.categories.create(category="venue", estimated_amount=400, actual_amount=300)
        self.budget.categories.create(category="catering", estimated_amount=600, actual_amount=900)

    def test_allocated_and_spent_sum_across_categories(self):
        self.assertEqual(self.budget.allocated, Decimal("1000"))
        self.assertEqual(self.budget.spent, Decimal("1200"))

    def test_remaining_goes_negative_when_overspent(self):
        self.assertEqual(self.budget.remaining, Decimal("-200"))

    def test_financial_status_flags_over_budget(self):
        # spent (1200) > total (1000) -> the red "Over Budget" badge.
        self.assertEqual(self.budget.financial_status, BudgetHealthStatus.OVER_BUDGET)
        self.assertEqual(self.budget.budget_health_percentage, Decimal("120.0"))

    def test_financial_status_on_track_and_not_set(self):
        on_track = EventBudget.objects.create(
            event=_make_event(
                User.objects.create_user(
                    first_name="Bo", last_name="Ade", email="b-client2@example.com", password="x",
                )
            ),
            total_budget=Decimal("1000"),
        )
        on_track.categories.create(category="venue", estimated_amount=400, actual_amount=200)
        self.assertEqual(on_track.financial_status, BudgetHealthStatus.ON_TRACK)

        not_set = EventBudget.objects.create(
            event=_make_event(
                User.objects.create_user(
                    first_name="Cy", last_name="Eze", email="b-client3@example.com", password="x",
                )
            ),
            total_budget=Decimal("0"),
        )
        self.assertEqual(not_set.financial_status, BudgetHealthStatus.NOT_SET)


class BudgetPaymentTotalsTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="b-pay@example.com", password="x",
        )
        self.budget = EventBudget.objects.create(
            event=_make_event(self.client_user), total_budget=Decimal("1000")
        )
        self.budget.payments.create(
            payment_date=datetime.date(2027, 1, 1), vendor_item="Decor Co", category="decor",
            purpose="Deposit", amount=Decimal("500"), status=PaymentStatus.PAID,
        )
        self.budget.payments.create(
            payment_date=datetime.date(2027, 2, 1), vendor_item="Catering Co", category="catering",
            purpose="Deposit", amount=Decimal("300"), status=PaymentStatus.PENDING,
        )

    def test_paid_and_pending_totals_and_counts(self):
        self.assertEqual(self.budget.payments_made_total, Decimal("500"))
        self.assertEqual(self.budget.payments_pending_total, Decimal("300"))
        self.assertEqual(self.budget.payments_made_count, 1)
        self.assertEqual(self.budget.payments_pending_count, 1)


class BudgetPermissionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            first_name="Win", last_name="Team", email="b-staff@example.com", password="x", role="staff",
        )
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="b-perm@example.com", password="x",
        )
        self.event = _make_event(self.client_user)

    def test_client_cannot_create_a_budget(self):
        req = factory.post("/", {"total_budget": "1000"}, format="json")
        force_authenticate(req, user=self.client_user)
        resp = views.budget_create(req, event_slug=self.event.slug)
        self.assertEqual(resp.status_code, 403)
