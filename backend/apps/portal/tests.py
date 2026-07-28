"""
apps/portal/tests.py

Covers engagement activation (docs/FAILURE_POINTS_AUDIT.md F5): switching which
event is active for a portal is non-destructive and reversible — it deactivates
the current engagement and (re)activates another, never deleting anything, and
never leaving more than one active engagement. Plus the content-summary counts
that back the "here's what's about to stop being shown" warning on a switch.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.events.models import Event
from apps.portal import services
from apps.portal.models import ClientPortal, EventEngagement

User = get_user_model()


def _event(celebrant, title):
    return Event.objects.create(
        celebrant=celebrant, title=title, event_type="Wedding",
        groom_name="Sam", bride_name="Pris", country="NG", state="Lagos",
        event_date=datetime.date(2027, 6, 1),
    )


class ActivateEngagementTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="p-client@example.com", password="x",
        )
        self.portal = ClientPortal.objects.get(user=self.client_user)
        self.event_a = _event(self.client_user, "Event A")
        self.event_b = _event(self.client_user, "Event B")
        self.eng_a = EventEngagement.objects.create(portal=self.portal, event=self.event_a, is_active=True)
        self.eng_b = EventEngagement.objects.create(portal=self.portal, event=self.event_b, is_active=False)

    def _active_count(self):
        return self.portal.engagements.filter(is_active=True).count()

    def test_activating_switches_the_active_engagement(self):
        services.activate_engagement(self.portal, self.event_b)
        self.eng_a.refresh_from_db()
        self.eng_b.refresh_from_db()
        self.assertTrue(self.eng_b.is_active)
        self.assertFalse(self.eng_a.is_active)
        self.assertEqual(self._active_count(), 1)

    def test_activating_reuses_the_existing_engagement(self):
        # get_or_create must reuse B's pre-staged engagement, not spawn a new one.
        services.activate_engagement(self.portal, self.event_b)
        self.assertEqual(self.portal.engagements.count(), 2)

    def test_switch_is_reversible(self):
        services.activate_engagement(self.portal, self.event_b)
        services.activate_engagement(self.portal, self.event_a)
        self.eng_a.refresh_from_db()
        self.eng_b.refresh_from_db()
        self.assertTrue(self.eng_a.is_active)
        self.assertFalse(self.eng_b.is_active)
        self.assertEqual(self._active_count(), 1)


class ContentSummaryTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="p-client2@example.com", password="x",
        )
        self.portal = ClientPortal.objects.get(user=self.client_user)
        self.event = _event(self.client_user, "Summary Event")
        self.engagement = EventEngagement.objects.create(
            portal=self.portal, event=self.event, is_active=True
        )

    def test_none_engagement_returns_empty(self):
        self.assertEqual(services.get_engagement_content_summary(None), {})

    def test_fresh_engagement_reports_all_zero_counts(self):
        summary = services.get_engagement_content_summary(self.engagement)
        # Every documented bucket is present and zero on a brand-new engagement.
        for key in (
            "meetings", "conversations", "reminders", "documents",
            "client_documents", "invoices", "receipts", "payment_milestones",
        ):
            self.assertEqual(summary[key], 0, f"expected {key} == 0")
