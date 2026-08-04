"""
apps/meetings/tests.py

Covers the meeting state machine (only whitelisted status transitions are
allowed), prep-item completion (an item with a required, unanswered field is
incomplete until answered), and the .ics "Add to Calendar" builder.
"""

import datetime

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.meetings import services
from apps.meetings.models import (
    FieldType,
    Meeting,
    MeetingPrepItem,
    MeetingStatus,
    PrepItemField,
    PrepItemResponse,
)
from apps.portal.models import PlanningPhase


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
