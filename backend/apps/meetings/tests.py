"""
apps/meetings/tests.py

Covers the meeting state machine (only whitelisted status transitions are
allowed), prep-item completion (an item with a required, unanswered field is
incomplete until answered), and the .ics "Add to Calendar" builder.
"""

import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError

from apps.events.models import Event
from apps.meetings import services
from apps.meetings.models import (
    FieldType,
    Meeting,
    MeetingPrepItem,
    MeetingStatus,
    PrepItemField,
    PrepItemResponse,
)
from apps.portal.models import ClientPortal, EventEngagement, PlanningPhase

User = get_user_model()


def _meeting(status=MeetingStatus.UPCOMING):
    return Meeting.objects.create(
        title="Kickoff", date=datetime.date(2027, 6, 1), time=datetime.time(10, 0),
        phase=PlanningPhase.CONNECT, status=status,
    )


class MeetingStatusTransitionTests(TestCase):
    def test_allowed_transition_succeeds(self):
        meeting = _meeting()
        services.transition_meeting_status(meeting, MeetingStatus.ACTIVE)
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, MeetingStatus.ACTIVE)

    def test_transition_from_terminal_state_is_rejected(self):
        meeting = _meeting(status=MeetingStatus.COMPLETED)  # terminal — no transitions out
        with self.assertRaises(ValidationError):
            services.transition_meeting_status(meeting, MeetingStatus.ACTIVE)


class PrepItemCompletionTests(TestCase):
    def test_required_field_gates_completion(self):
        meeting = _meeting()
        item = MeetingPrepItem.objects.create(meeting=meeting, title="Bring references")
        field = PrepItemField.objects.create(
            prep_item=item, field_type=FieldType.TEXT, label="Notes", is_required=True,
        )

        services.sync_prep_item_completion(item)
        item.refresh_from_db()
        self.assertFalse(item.is_completed)  # required field unanswered

        PrepItemResponse.objects.create(field=field, text_value="here are my notes")
        services.sync_prep_item_completion(item)
        item.refresh_from_db()
        self.assertTrue(item.is_completed)


class BuildIcsTests(TestCase):
    def test_ics_is_a_valid_single_vevent_with_the_title(self):
        data = services.build_ics(_meeting())
        self.assertIsInstance(data, bytes)
        self.assertIn(b"BEGIN:VCALENDAR", data)
        self.assertIn(b"BEGIN:VEVENT", data)
        self.assertIn(b"SUMMARY:Kickoff", data)
        self.assertIn(b"END:VCALENDAR", data)


class MeetingPrepDigestTimezoneTests(TestCase):
    """
    P2-9 for the meeting-prep digest. Same reasoning as the payment digest, but
    this one has a **lower** bound that matters: a meeting must never be nudged
    after it has already happened, so the window is closed at both ends and the
    widened query has to reach a day back for a client whose today is still
    yesterday in UTC.
    """

    INSTANT = datetime.datetime(2026, 10, 10, 23, 30, tzinfo=datetime.timezone.utc)
    EAST_TZ = "Pacific/Auckland"   # UTC+13 in October -> local 2026-10-11
    WEST_TZ = "Pacific/Midway"     # UTC-11           -> local 2026-10-10

    def _engagement(self, email, tz):
        user = User.objects.create_user(
            first_name="TZ", last_name="Meet", email=email, password="x",
        )
        user.timezone = tz
        user.save(update_fields=["timezone"])

        portal = ClientPortal.objects.get(user=user)
        event = Event.objects.create(
            celebrant=user, title=f"Meet Wedding {email}", event_type="Wedding",
            bride_name="A", groom_name="B", country="NG", state="Lagos",
            event_date=datetime.date(2027, 3, 1),
        )
        return EventEngagement.objects.create(
            portal=portal, event=event, is_active=True, current_phase="connect",
        )

    def _meeting_needing_prep(self, engagement, date):
        meeting = Meeting.objects.create(
            engagement=engagement, title="Prep me", date=date,
            time=datetime.time(10, 0), phase=PlanningPhase.CONNECT,
            status=MeetingStatus.UPCOMING, preparation_required=True,
        )
        # An incomplete prep item is what makes the meeting digest-worthy.
        MeetingPrepItem.objects.create(meeting=meeting, title="Bring the deck")
        return meeting

    def _run(self, instant=None):
        from apps.meetings.tasks import meeting_prep_digest_task
        with mock.patch("django.utils.timezone.now", return_value=instant or self.INSTANT):
            with mock.patch("apps.notifications.services._send_via_brevo"):
                meeting_prep_digest_task()

    def test_a_meeting_already_past_for_the_client_is_not_nudged(self):
        """The lower bound. A meeting on the 10th is TODAY for Midway but
        YESTERDAY for Auckland — nudging Auckland about it would be a reminder to
        prepare for something that has happened."""
        east = self._engagement("east-meet@example.com", self.EAST_TZ)
        west = self._engagement("west-meet@example.com", self.WEST_TZ)

        east_meeting = self._meeting_needing_prep(east, datetime.date(2026, 10, 10))
        west_meeting = self._meeting_needing_prep(west, datetime.date(2026, 10, 10))

        self._run()

        east_meeting.refresh_from_db()
        west_meeting.refresh_from_db()
        self.assertIsNone(east_meeting.prep_reminder_sent_at)      # already past
        self.assertIsNotNone(west_meeting.prep_reminder_sent_at)   # today

    def test_the_upper_boundary_is_measured_in_the_clients_calendar(self):
        east = self._engagement("east-far@example.com", self.EAST_TZ)
        west = self._engagement("west-far@example.com", self.WEST_TZ)

        east_meeting = self._meeting_needing_prep(east, datetime.date(2026, 10, 14))
        west_meeting = self._meeting_needing_prep(west, datetime.date(2026, 10, 14))

        self._run()

        east_meeting.refresh_from_db()
        west_meeting.refresh_from_db()
        self.assertIsNotNone(east_meeting.prep_reminder_sent_at)  # 3 days out locally
        self.assertIsNone(west_meeting.prep_reminder_sent_at)     # still 4 days out

    def test_a_meeting_today_for_the_client_is_nudged(self):
        east = self._engagement("east-today@example.com", self.EAST_TZ)
        meeting = self._meeting_needing_prep(east, datetime.date(2026, 10, 11))

        self._run()

        meeting.refresh_from_db()
        self.assertIsNotNone(meeting.prep_reminder_sent_at)

    def test_a_client_with_no_timezone_uses_the_platform_default(self):
        engagement = self._engagement("meet-default@example.com", "")
        meeting = self._meeting_needing_prep(engagement, datetime.date(2026, 10, 11))

        with override_settings(PLATFORM_DEFAULT_TIMEZONE=self.EAST_TZ):
            self._run()

        meeting.refresh_from_db()
        self.assertIsNotNone(meeting.prep_reminder_sent_at)
