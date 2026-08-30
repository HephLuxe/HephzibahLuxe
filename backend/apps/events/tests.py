"""
apps/events/tests.py

Locks down the behaviours the failure audit (docs/FAILURE_POINTS_AUDIT.md) fixed
by hand, so a future refactor can't silently reopen them:

  * F1 — destructive delete is gated behind an impact preview + ?confirm=true.
  * F2 — clients can never delete an event (staff-only, regardless of locks).
  * F3 (addendum) — EVERY event gets an engagement; the first is active, later
    ones inactive (so a 2nd event can be pre-staged, never "ghosted").

Plus the event-details notification debounce, which became a durable sweep when
Celery was removed (docs/adr/0001-remove-celery.md): the whole schedule is now
three columns on EventEngagement rather than a column plus a broker message with
a countdown on it.

Plus the media-pipeline change: image fields render ABSOLUTE URLs when the view
threads the request into the serializer context (so a separate frontend origin
can resolve them under local storage; with R2 they're absolute regardless).

Views are function-based, so they're driven with APIRequestFactory +
force_authenticate and called directly — the same pattern accounts/tests.py uses.
"""

import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.storage import InMemoryStorage
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core import background
from apps.core.error_codes import CONFIRMATION_REQUIRED
from apps.core.pagination import StandardPageNumberPagination
from apps.events import views
from apps.events.models import Event, EventDay
from apps.events.serializers import EventSerializer
from apps.events.services import schedule_event_details_notification
from apps.events.tasks import dispatch_due_event_details_notifications
from apps.notifications.models import Notification
from apps.portal.models import ClientPortal

User = get_user_model()
factory = APIRequestFactory()


def _make_event(celebrant, title="Sam & Pris's Wedding"):
    return Event.objects.create(
        celebrant=celebrant, title=title, event_type="Wedding",
        groom_name="Sam", bride_name="Pris", country="NG", state="Lagos",
        event_date=datetime.date(2027, 6, 1),
    )


class EventDeletePermissionTests(TestCase):
    """F1/F2 — who can delete, and the confirmation gate on destructive deletes."""

    def setUp(self):
        self.staff = User.objects.create_user(
            first_name="Win", last_name="Team", email="staff@example.com",
            password="x", role="staff",
        )
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="client@example.com", password="x",
        )
        self.event = _make_event(self.client_user)

    def _delete(self, user, slug, confirm=False):
        req = factory.delete("/?confirm=true" if confirm else "/")
        force_authenticate(req, user=user)
        return views.delete_event(req, slug=slug)

    def test_client_cannot_delete_their_own_event(self):
        # F2: even the event's own celebrant is refused — deletes are staff-only.
        resp = self._delete(self.client_user, self.event.slug)
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_staff_can_delete_an_empty_event_without_confirmation(self):
        resp = self._delete(self.staff, self.event.slug)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())

    def test_delete_with_related_data_requires_confirm(self):
        # F1: a non-empty event refuses to delete without ?confirm=true, and the
        # response carries the impact breakdown so the operator can see the blast.
        EventDay.objects.create(owner=self.event, date=datetime.date(2027, 6, 1))

        blocked = self._delete(self.staff, self.event.slug)
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.data["code"], CONFIRMATION_REQUIRED)
        self.assertGreater(blocked.data["errors"]["impact"]["total"], 0)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

        confirmed = self._delete(self.staff, self.event.slug, confirm=True)
        self.assertEqual(confirmed.status_code, 200)
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())

    def test_delete_impact_preview_counts_related_without_deleting(self):
        EventDay.objects.create(owner=self.event, date=datetime.date(2027, 6, 1))
        req = factory.get("/")
        force_authenticate(req, user=self.staff)
        resp = views.get_event_delete_impact(req, slug=self.event.slug)
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(resp.data["total"], 0)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())  # preview, not delete


class EventEngagementAutoCreateTests(TestCase):
    """F3 addendum — every event gets an engagement; only the first is active."""

    def setUp(self):
        self.staff = User.objects.create_user(
            first_name="Win", last_name="Team", email="staff2@example.com",
            password="x", role="staff",
        )
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="client2@example.com", password="x",
        )
        self.portal = ClientPortal.objects.get(user=self.client_user)

    def _create_event(self):
        req = factory.post("/", {
            "user_email": self.client_user.email, "event_type": "Wedding",
            "groom_name": "Sam", "bride_name": "Pris", "country": "NG",
            "state": "Lagos", "event_date": "2027-06-01",
        }, format="json")
        force_authenticate(req, user=self.staff)
        return views.create_event(req)

    def test_first_event_gets_an_active_engagement(self):
        resp = self._create_event()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self.portal.engagements.count(), 1)
        self.assertEqual(self.portal.engagements.filter(is_active=True).count(), 1)

    def test_second_event_gets_an_inactive_engagement(self):
        self._create_event()
        resp = self._create_event()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self.portal.engagements.count(), 2)
        # Still exactly one active — the 2nd event's engagement is inactive.
        self.assertEqual(self.portal.engagements.filter(is_active=True).count(), 1)


class EventImageUrlTests(TestCase):
    """
    The media-pipeline fix: threading the request into the serializer context
    yields an ABSOLUTE image URL. Pinned to in-memory storage for the duration
    so the assertion is deterministic regardless of whether the .env has R2
    enabled — with R2 the URL is absolute either way; a relative-URL storage is
    exactly the case where the request context is what makes it absolute.
    """

    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="client3@example.com", password="x",
        )
        self.event = _make_event(self.client_user)

    def test_request_context_absolutizes_the_image_url(self):
        field = Event._meta.get_field("featured_image")
        with mock.patch.object(field, "storage", InMemoryStorage()):
            # The FieldFile captures field.storage at assignment — set it here.
            self.event.featured_image = "portals/test/events/1-x/covers/cover.jpg"
            without_ctx = EventSerializer(self.event).data["featured_image"]
            with_ctx = EventSerializer(
                self.event, context={"request": factory.get("/")}
            ).data["featured_image"]
        self.assertTrue(without_ctx.startswith("/media/"))  # relative without a request
        self.assertTrue(with_ctx.startswith("http"))        # absolutized via the request


class EventDetailsDebounceTests(TestCase):
    """
    The debounce is now entirely a row: `event_details_notify_due_at` says when,
    `_what` says what changed, and a cron sweep sends whatever is due. Previously
    half that state lived in a Celery `apply_async(countdown=...)`, so a worker
    restart or a deploy inside the window could drop the email outright.
    """

    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Sam", last_name="Client", email="sam@example.com",
            password="x", role="client",
        )
        # activate_engagement is what the create_event view calls; going
        # through it keeps this fixture honest rather than hand-building a row.
        from apps.portal.services import activate_engagement

        portal = ClientPortal.objects.get(user=self.client_user)
        self.event = _make_event(self.client_user)
        self.engagement = activate_engagement(portal, self.event)

    def _schedule(self, what="the event date"):
        from apps.events.services import schedule_event_details_notification
        schedule_event_details_notification(self.event, what)
        self.engagement.refresh_from_db()

    def test_scheduling_stamps_the_whole_schedule_on_the_row(self):
        self._schedule("the venue")

        self.assertIsNotNone(self.engagement.event_details_notify_due_at)
        self.assertIsNotNone(self.engagement.event_details_notify_token)
        self.assertEqual(self.engagement.event_details_notify_what, "the venue")

    def test_a_later_edit_pushes_the_due_time_out_and_wins(self):
        """The debounce itself: editing again while pending must delay the send
        and describe the newer change, not queue a second email."""
        self._schedule("the venue")
        first_due = self.engagement.event_details_notify_due_at

        self._schedule("the guest count")

        self.assertGreater(self.engagement.event_details_notify_due_at, first_due)
        self.assertEqual(self.engagement.event_details_notify_what, "the guest count")

    def test_the_sweep_leaves_a_pending_row_alone(self):
        from apps.events.tasks import dispatch_due_event_details_notifications
        from apps.notifications.models import Notification

        self._schedule()

        dispatch_due_event_details_notifications()

        self.engagement.refresh_from_db()
        self.assertIsNotNone(self.engagement.event_details_notify_due_at)
        self.assertFalse(
            Notification.objects.filter(template_name="event_details_updated").exists()
        )

    @mock.patch("apps.notifications.services._send_via_brevo")
    def test_the_sweep_sends_a_due_row_and_clears_the_schedule(self, _mock_send):
        from django.utils import timezone

        from apps.events.tasks import dispatch_due_event_details_notifications
        from apps.notifications.models import Notification
        from apps.portal.models import EventEngagement

        self._schedule("the event date")
        EventEngagement.objects.filter(pk=self.engagement.pk).update(
            event_details_notify_due_at=timezone.now() - datetime.timedelta(seconds=1)
        )

        dispatch_due_event_details_notifications()

        notification = Notification.objects.get(template_name="event_details_updated")
        self.assertEqual(notification.recipient_email, self.client_user.email)
        self.assertEqual(notification.context["what"], "the event date")

        self.engagement.refresh_from_db()
        self.assertIsNone(self.engagement.event_details_notify_due_at)
        self.assertIsNone(self.engagement.event_details_notify_token)
        self.assertEqual(self.engagement.event_details_notify_what, "")

    @mock.patch("apps.notifications.services._send_via_brevo")
    def test_a_second_sweep_does_not_send_again(self, _mock_send):
        """The claim-then-send ordering: clearing the schedule first is what makes
        the sweep idempotent, so a cron overlap cannot double-mail a client."""
        from django.utils import timezone

        from apps.events.tasks import dispatch_due_event_details_notifications
        from apps.notifications.models import Notification
        from apps.portal.models import EventEngagement

        self._schedule()
        EventEngagement.objects.filter(pk=self.engagement.pk).update(
            event_details_notify_due_at=timezone.now() - datetime.timedelta(seconds=1)
        )

        dispatch_due_event_details_notifications()
        dispatch_due_event_details_notifications()

        self.assertEqual(
            Notification.objects.filter(template_name="event_details_updated").count(), 1
        )

    def test_the_admin_kill_switch_stops_scheduling(self):
        from apps.notifications.models import ScheduledTaskSettings

        ScheduledTaskSettings.objects.update_or_create(
            task_key="event_details_notification",
            defaults={"label": "event details", "is_enabled": False},
        )

        self._schedule()

        self.assertIsNone(self.engagement.event_details_notify_due_at)

    def test_the_admin_kill_switch_also_stops_an_already_pending_row(self):
        """A row stamped before the switch was flipped off must not fire either."""
        from django.utils import timezone

        from apps.events.tasks import dispatch_due_event_details_notifications
        from apps.notifications.models import Notification, ScheduledTaskSettings
        from apps.portal.models import EventEngagement

        self._schedule()
        EventEngagement.objects.filter(pk=self.engagement.pk).update(
            event_details_notify_due_at=timezone.now() - datetime.timedelta(seconds=1)
        )
        ScheduledTaskSettings.objects.update_or_create(
            task_key="event_details_notification",
            defaults={"label": "event details", "is_enabled": False},
        )

        dispatch_due_event_details_notifications()

        self.assertFalse(
            Notification.objects.filter(template_name="event_details_updated").exists()
        )


@override_settings(BACKGROUND_EAGER=False)
class EventDetailsDebounceTimerTests(TestCase):
    """
    The in-process timer that makes the debounce punctual (ADR-0001, option C).

    The rule these pin: the timer changes WHEN the sweep is observed, never when
    the work becomes due. Every reset semantic still lives in
    `event_details_notify_due_at`.
    """

    def setUp(self):
        from apps.portal.services import activate_engagement

        background.cancel_timers()
        self.addCleanup(background.cancel_timers)
        self.addCleanup(background.disable_async)
        background.enable_async()

        self.client_user = User.objects.create_user(
            first_name="Sam", last_name="Client", email="timer@example.com",
            password="x", role="client",
        )
        portal = ClientPortal.objects.get(user=self.client_user)
        self.event = _make_event(self.client_user)
        self.engagement = activate_engagement(portal, self.event)

    def test_an_edit_arms_exactly_one_timer(self):
        schedule_event_details_notification(self.event, "the venue")
        self.assertEqual(background.armed_timers(), 1)

    def test_a_burst_of_edits_leaves_one_timer_not_one_per_edit(self):
        """The behaviour that makes arming-per-edit affordable: the key is the
        engagement, so re-arming replaces."""
        for n in range(15):
            schedule_event_details_notification(self.event, f"edit {n}")
        self.assertEqual(background.armed_timers(), 1)

    def test_a_later_edit_pushes_the_due_time_out(self):
        """The reset the planner relies on: edit at minute 13 of a 15-minute
        window and the clock starts again from there, so the client is emailed
        15 minutes after the LAST edit, not the first."""
        schedule_event_details_notification(self.event, "first")
        self.engagement.refresh_from_db()
        first_due = self.engagement.event_details_notify_due_at

        schedule_event_details_notification(self.event, "second")
        self.engagement.refresh_from_db()

        self.assertGreater(self.engagement.event_details_notify_due_at, first_due)
        self.assertEqual(self.engagement.event_details_notify_what, "second")

    def test_a_timer_firing_early_sends_nothing(self):
        """A timer armed by the FIRST edit wakes while a later edit has pushed
        due_at into the future. It must find no work — which the sweep's own
        `due_at <= now` filter already guarantees, so the timer needs no
        cancellation logic or token comparison of its own."""
        schedule_event_details_notification(self.event, "first")
        # A second edit lands, moving the deadline well out.
        schedule_event_details_notification(self.event, "second")

        # Now run the sweep as the stale timer would have.
        dispatch_due_event_details_notifications()

        self.assertEqual(Notification.objects.count(), 0)
        self.engagement.refresh_from_db()
        self.assertIsNotNone(self.engagement.event_details_notify_due_at)

    def test_the_timer_and_the_cron_sweep_cannot_both_send(self):
        """Both runners call the same sweep, and the conditional UPDATE claims
        the row — whichever arrives first wins. This is why arming a timer
        needed no new concurrency reasoning."""
        schedule_event_details_notification(self.event, "the venue")
        self.engagement.event_details_notify_due_at = timezone.now() - datetime.timedelta(seconds=1)
        self.engagement.save(update_fields=["event_details_notify_due_at"])

        dispatch_due_event_details_notifications()   # the timer
        dispatch_due_event_details_notifications()   # the cron sweep

        self.assertEqual(Notification.objects.count(), 1)

    def test_the_row_is_still_the_guarantee_when_no_timer_was_armed(self):
        """A restart, a deploy, or the timer cap: the sweep delivers regardless.
        Simulated by scheduling with async off, so nothing is ever armed."""
        background.disable_async()
        schedule_event_details_notification(self.event, "the venue")
        self.assertEqual(background.armed_timers(), 0)

        self.engagement.refresh_from_db()
        self.assertIsNotNone(self.engagement.event_details_notify_due_at)

        self.engagement.event_details_notify_due_at = timezone.now() - datetime.timedelta(seconds=1)
        self.engagement.save(update_fields=["event_details_notify_due_at"])
        dispatch_due_event_details_notifications()

        self.assertEqual(Notification.objects.count(), 1)


class EventListsAreAlwaysBoundedTests(TestCase):
    """
    GET /event/all and /event/event_day/all used to serialise the whole table
    unless the caller opted into pagination with ?page= / ?page_size=.

    A rate limit bounds how many requests a caller makes, not how much each one
    hands over — so for a staff token, one request returned every event on the
    platform. These pin that the DEFAULT path is bounded, since the default is
    the one an attacker uses.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            first_name="Win", last_name="Team", email="staff@example.com",
            password="x", role="staff",
        )
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="client@example.com", password="x",
        )
        # Comfortably more than StandardPageNumberPagination.page_size (7).
        self.events = [
            _make_event(self.client_user, title=f"Event {n}") for n in range(12)
        ]

    def _get(self, view, user, query=""):
        req = factory.get(f"/{query}")
        force_authenticate(req, user=user)
        return view(req)

    def test_the_default_response_is_paginated(self):
        """No query params at all — the path a caller gets by just asking."""
        resp = self._get(views.getall_event, self.staff)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            sorted(resp.data.keys()), ["count", "next", "previous", "results"],
        )

    def test_one_request_cannot_take_the_whole_table(self):
        """The actual property being defended. 12 events exist; a single default
        request must not hand over 12."""
        resp = self._get(views.getall_event, self.staff)
        self.assertEqual(resp.data["count"], 12)      # it still SAYS 12
        self.assertEqual(len(resp.data["results"]), 7)  # it does not SEND 12

    def test_event_days_are_bounded_the_same_way(self):
        for event in self.events:
            EventDay.objects.create(
                owner=event, event_day_title="Ceremony", date=datetime.date(2027, 6, 1),
            )
        resp = self._get(views.getall_eventday, self.staff)
        self.assertEqual(resp.data["count"], 12)
        self.assertEqual(len(resp.data["results"]), 7)

    def test_the_rest_is_still_reachable_by_paging(self):
        """Bounded, not truncated — nothing is hidden, it just costs a request
        per page. A cap with no way through would be data loss."""
        seen = []
        page = 1
        while True:
            resp = self._get(views.getall_event, self.staff, f"?page={page}")
            seen.extend(r["id"] for r in resp.data["results"])
            if not resp.data["next"]:
                break
            page += 1
        self.assertEqual(len(seen), 12)
        self.assertEqual(len(set(seen)), 12)  # no page overlap or gap

    def test_page_size_is_capped(self):
        """?page_size= widens the page but cannot reopen the hole — otherwise
        `?page_size=100000` is the old unbounded response with extra steps."""
        resp = self._get(views.getall_event, self.staff, "?page_size=100000")
        self.assertLessEqual(
            len(resp.data["results"]), StandardPageNumberPagination.max_page_size,
        )

    def test_a_client_still_only_sees_their_own(self):
        """Pagination must not disturb the role scoping underneath it."""
        stranger = User.objects.create_user(
            first_name="No", last_name="One", email="stranger@example.com", password="x",
        )
        resp = self._get(views.getall_event, stranger)
        self.assertEqual(resp.data["count"], 0)

        mine = self._get(views.getall_event, self.client_user)
        self.assertEqual(mine.data["count"], 12)
