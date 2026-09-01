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
import io
from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core import background
from apps.core.error_codes import CONFIRMATION_REQUIRED
from apps.core.pagination import StandardPageNumberPagination
from apps.core.utils import event_gallery_upload_path
from apps.events import services, views
from apps.events.models import Event, EventDay, EventImage
from apps.events.serializers import EventDaySerializer, EventSerializer
from apps.events.services import get_event_deletion_impact, schedule_event_details_notification
from apps.events.tasks import dispatch_due_event_details_notifications
from apps.notifications.models import Notification
from apps.portal.models import ClientPortal, EventEngagement

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

    Asserted through the gallery now that `Event.featured_image` is gone, and
    through `cover_image` specifically — that is the field callers read in its
    place, so it is the one that has to come back absolute.
    """

    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="client3@example.com", password="x",
        )
        self.event = _make_event(self.client_user)

    def test_request_context_absolutizes_the_image_url(self):
        field = EventImage._meta.get_field("image")
        with mock.patch.object(field, "storage", InMemoryStorage()):
            # The FieldFile captures field.storage at assignment — set it here.
            EventImage.objects.create(
                event=self.event, image="portals/test/events/1-x/gallery/a/cover.jpg",
                is_primary=True,
            )
            without_ctx = EventSerializer(self.event).data["cover_image"]["image"]
            with_ctx = EventSerializer(
                self.event, context={"request": factory.get("/")}
            ).data["cover_image"]["image"]
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


class PublicPageCopyFieldTests(TestCase):
    """
    The public page renders three separate pieces of text per event day — eyebrow,
    headline, narrative — and `EventDay` used to carry only two text columns, one
    of which (`content`) had no defined meaning and no reader anywhere in the
    codebase. So the headline had nowhere to live and never appeared in the API
    response. These lock in the split.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            first_name="Win", last_name="Team", email="copystaff@example.com",
            password="x", role="staff",
        )
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="copyclient@example.com", password="x",
        )
        self.event = _make_event(self.client_user)

    EYEBROW = "Pre-Birthday Photoshoot"
    HEADLINE = "A Moment Before Fifty — A Pre-Birthday Portrait Experience"
    NARRATIVE = (
        "Before the celebrations began, there was a quiet moment to pause—to honour "
        "the woman at the heart of it all and the milestone she was about to embrace.\n\n"
        "Rather than relying on an elaborate setting, the experience remained "
        "intentionally understated."
    )

    def test_an_event_day_round_trips_all_three_pieces_of_copy(self):
        """The gap this closes: `headline` had no column, so a create that sent it
        silently dropped it and the response came back without it."""
        req = factory.post("/", {
            "event_day_title": self.EYEBROW,
            "headline": self.HEADLINE,
            "content": self.NARRATIVE,
            "date": "2026-11-27",
        }, format="json")
        force_authenticate(req, user=self.staff)
        resp = views.create_eventday(req, event_slug=self.event.slug)

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["event_day_title"], self.EYEBROW)
        self.assertEqual(resp.data["headline"], self.HEADLINE)
        self.assertEqual(resp.data["content"], self.NARRATIVE)

        day = EventDay.objects.get(id=resp.data["id"])
        self.assertEqual(day.headline, self.HEADLINE)

    def test_the_headline_is_patchable_on_its_own(self):
        day = EventDay.objects.create(
            owner=self.event, event_day_title="Event No. 1", date=datetime.date(2026, 11, 27),
        )
        req = factory.patch("/", {"headline": self.HEADLINE}, format="json")
        force_authenticate(req, user=self.staff)
        resp = views.update_eventday(req, event_slug=self.event.slug, id=day.id)

        self.assertEqual(resp.status_code, 200)
        day.refresh_from_db()
        self.assertEqual(day.headline, self.HEADLINE)
        self.assertEqual(day.event_day_title, "Event No. 1")  # eyebrow untouched

    def test_the_event_carries_its_own_headline_beside_the_derived_title(self):
        """`Event.title` is generated from the celebrant names and the portal depends
        on that mechanical form, so the editorial line needs a separate field rather
        than overwriting it."""
        req = factory.patch("/", {
            "headline": "A Golden 50th: An Intimate Two-Day Celebration of Family, Faith & Joy",
        }, format="json")
        force_authenticate(req, user=self.staff)
        resp = views.update_event(req, slug=self.event.slug)

        self.assertEqual(resp.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(
            self.event.headline,
            "A Golden 50th: An Intimate Two-Day Celebration of Family, Faith & Joy",
        )
        self.assertEqual(self.event.title, "Sam & Pris's Wedding")  # derived title intact

    def test_short_legacy_content_values_are_still_valid(self):
        """`content` was redefined as the long-form narrative rather than given a
        sibling summary field. Rows written before it had a defined meaning hold
        one-liners; those must not need a backfill."""
        day = EventDay.objects.create(
            owner=self.event, event_day_title="Traditional Wedding",
            date=datetime.date(2026, 11, 27),
            content="Traditional engagement ceremony with both families.",
        )
        day.full_clean()  # no length floor, no required headline

    def test_day_number_does_not_match_the_public_numbering(self):
        """Why the eyebrow is typed rather than derived: the photoshoot sorts first
        by date but sits OUTSIDE the numbered sequence, so `day_number` would render
        the first celebration day as 'No. 2'."""
        shoot = EventDay.objects.create(
            owner=self.event, event_day_title="Pre-Birthday Photoshoot",
            date=datetime.date(2026, 11, 20),
        )
        first_celebration = EventDay.objects.create(
            owner=self.event, event_day_title="Event No. 1",
            date=datetime.date(2026, 11, 27),
        )
        self.assertEqual(shoot.day_number, 1)
        self.assertEqual(first_celebration.day_number, 2)  # but it is labelled "No. 1"


def _png(name="photo.png"):
    """A real PNG — DRF's ImageField runs Pillow before any custom validation,
    so junk bytes would be rejected for the wrong reason."""
    buf = io.BytesIO()
    Image.new("RGB", (2, 2)).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


class EventGalleryTests(TestCase):
    """
    The gallery upgrade. `Event.featured_image` and `EventDay.event_images` each
    held exactly ONE image, so staff had one shot at a cover and a day's
    photographs had nowhere to live at all. Both are now EventImage rows, told
    apart by whether `event_day` is set, with the `is_primary` row serving as the
    cover the public page renders.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            first_name="Win", last_name="Team", email="gallerystaff@example.com",
            password="x", role="staff",
        )
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="galleryclient@example.com", password="x",
        )
        self.event = _make_event(self.client_user)
        self.day = EventDay.objects.create(
            owner=self.event, event_day_title="Pre-Birthday Photoshoot",
            date=datetime.date(2026, 11, 20),
        )

    def _upload(self, files, event_day=None, user=None):
        body = {"image": files}
        if event_day:
            body["event_day"] = str(event_day.id)
        req = factory.post("/", body, format="multipart")
        force_authenticate(req, user=user or self.staff)
        return views.event_gallery(req, event_slug=self.event.slug)

    # ── the shape of the thing ────────────────────────────────────────────────

    def test_an_event_holds_many_images_and_one_is_the_cover(self):
        resp = self._upload([_png("a.png"), _png("b.png"), _png("c.png")])
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.data), 3)
        self.assertEqual(self.event.images.count(), 3)
        # The frontend renders one; staff keep the alternates.
        self.assertEqual(self.event.images.filter(is_primary=True).count(), 1)

    def test_a_day_holds_its_own_gallery_separate_from_the_events(self):
        self._upload([_png("event.png")])
        self._upload([_png("day1.png"), _png("day2.png")], event_day=self.day)

        event_level = self.event.images.filter(event_day__isnull=True)
        self.assertEqual(event_level.count(), 1)
        self.assertEqual(self.day.images.count(), 2)

        # The event's serialized gallery must NOT contain the day's photographs,
        # or every day image would appear twice in one detail response.
        data = EventSerializer(self.event, context={"request": factory.get("/")}).data
        self.assertEqual(len(data["images"]), 1)
        self.assertEqual(data["cover_image"]["id"], str(event_level.first().id))

    def test_the_day_cover_is_the_days_primary_not_the_events(self):
        self._upload([_png("event.png")])
        self._upload([_png("day.png")], event_day=self.day)

        day_data = EventDaySerializer(self.day, context={"request": factory.get("/")}).data
        self.assertEqual(day_data["cover_image"]["id"], str(self.day.images.first().id))
        self.assertTrue(day_data["cover_image"]["is_primary"])

    def test_the_first_upload_becomes_the_cover_automatically(self):
        """Otherwise an event with photographs but no primary renders an empty
        tile — indistinguishable, to the frontend, from having no photographs."""
        resp = self._upload([_png("only.png")])
        self.assertTrue(resp.data[0]["is_primary"])

    # ── the primary slot ─────────────────────────────────────────────────────

    def test_promoting_a_new_cover_demotes_the_old_one(self):
        """The two-row half of `is_primary`. A partial unique constraint refuses a
        second primary, so a naive `is_primary = True` save would 500 instead of
        swapping."""
        self._upload([_png("a.png"), _png("b.png")])
        first, second = list(self.event.images.all())
        self.assertTrue(first.is_primary)

        req = factory.patch("/", {"is_primary": True}, format="json")
        force_authenticate(req, user=self.staff)
        resp = views.event_gallery_image(req, event_slug=self.event.slug, image_id=second.id)

        self.assertEqual(resp.status_code, 200)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_primary)
        self.assertTrue(second.is_primary)
        self.assertEqual(self.event.images.filter(is_primary=True).count(), 1)

    def test_promoting_the_current_cover_is_a_no_op_not_a_wipe(self):
        """Idempotence. Clearing the scope before setting the flag would leave the
        gallery coverless if it didn't exclude the image being promoted."""
        self._upload([_png("a.png")])
        image = self.event.images.first()

        services.set_primary_image(image)

        image.refresh_from_db()
        self.assertTrue(image.is_primary)

    def test_an_event_primary_and_a_day_primary_do_not_compete(self):
        """The `event_day__isnull=True` arm of the event constraint. Without it a
        day cover would occupy its event's only primary slot."""
        self._upload([_png("event.png")])
        self._upload([_png("day.png")], event_day=self.day)

        self.assertEqual(
            self.event.images.filter(is_primary=True, event_day__isnull=True).count(), 1,
        )
        self.assertEqual(self.day.images.filter(is_primary=True).count(), 1)

    def test_deleting_the_cover_promotes_the_next_image(self):
        self._upload([_png("a.png"), _png("b.png")])
        cover = self.event.images.get(is_primary=True)

        req = factory.delete("/")
        force_authenticate(req, user=self.staff)
        resp = views.event_gallery_image(req, event_slug=self.event.slug, image_id=cover.id)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.event.images.count(), 1)
        self.assertTrue(self.event.images.first().is_primary)

    # ── ordering ─────────────────────────────────────────────────────────────

    def test_uploads_queue_at_the_end_rather_than_colliding_on_zero(self):
        self._upload([_png("a.png")])
        self._upload([_png("b.png")])
        self._upload([_png("c.png")])
        self.assertEqual(
            [i.sort_order for i in self.event.images.all()], [0, 1, 2],
        )

    def test_reorder_sets_the_whole_gallery_in_one_call(self):
        self._upload([_png("a.png"), _png("b.png"), _png("c.png")])
        ids = [str(i.id) for i in self.event.images.all()]
        reversed_ids = list(reversed(ids))

        req = factory.post("/", {"image_ids": reversed_ids}, format="json")
        force_authenticate(req, user=self.staff)
        resp = views.reorder_event_gallery(req, event_slug=self.event.slug)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual([img["id"] for img in resp.data], reversed_ids)

    def test_a_partial_reorder_is_refused(self):
        """A list missing an image would silently leave it at its old position,
        which after a drag-and-drop is an order nobody asked for."""
        self._upload([_png("a.png"), _png("b.png")])
        one_id = str(self.event.images.first().id)

        req = factory.post("/", {"image_ids": [one_id]}, format="json")
        force_authenticate(req, user=self.staff)
        resp = views.reorder_event_gallery(req, event_slug=self.event.slug)

        self.assertEqual(resp.status_code, 400)

    # ── scoping and permissions ──────────────────────────────────────────────

    def test_an_image_cannot_be_attached_to_another_events_day(self):
        """`event_day` is a bare id in the body; without a membership check it
        would attach an image to any day on the platform."""
        other_event = _make_event(self.client_user, title="Someone Else's Wedding")
        foreign_day = EventDay.objects.create(
            owner=other_event, event_day_title="Theirs", date=datetime.date(2027, 1, 1),
        )
        resp = self._upload([_png("a.png")], event_day=foreign_day)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(EventImage.objects.count(), 0)

    def test_an_image_from_another_event_is_not_reachable_by_id(self):
        other_event = _make_event(self.client_user, title="Another Wedding")
        foreign = EventImage.objects.create(event=other_event, image="x/y.png")

        req = factory.delete("/")
        force_authenticate(req, user=self.staff)
        resp = views.event_gallery_image(
            req, event_slug=self.event.slug, image_id=foreign.id,
        )
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(EventImage.objects.filter(pk=foreign.pk).exists())

    def test_a_batch_with_one_bad_file_uploads_none_of_it(self):
        """Validated as a set, so the caller isn't left guessing how many of five
        photographs landed.

        The bad file is junk bytes rather than an oversized one: inflating
        `.size` (how core/tests.py reaches the ceiling) does not survive
        APIRequestFactory, which encodes the upload into a body that DRF then
        re-parses at its true size. The size ceiling itself is covered in
        core/tests.py at the serializer; what matters here is that ANY invalid
        file in the batch stops the whole batch.
        """
        junk = SimpleUploadedFile("broken.png", b"not an image", content_type="image/png")
        resp = self._upload([_png("fine.png"), junk])

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.event.images.count(), 0)

    def test_an_upload_with_no_file_is_a_400_not_a_201(self):
        req = factory.post("/", {}, format="multipart")
        force_authenticate(req, user=self.staff)
        resp = views.event_gallery(req, event_slug=self.event.slug)
        self.assertEqual(resp.status_code, 400)

    def test_a_locked_event_refuses_client_uploads_but_not_staff(self):
        portal = ClientPortal.objects.get(user=self.client_user)
        EventEngagement.objects.create(
            portal=portal, event=self.event, is_active=True, event_details_locked=True,
        )

        blocked = self._upload([_png("a.png")], user=self.client_user)
        self.assertEqual(blocked.status_code, 403)

        allowed = self._upload([_png("a.png")], user=self.staff)
        self.assertEqual(allowed.status_code, 201)

    # ── cascades ─────────────────────────────────────────────────────────────

    def test_deleting_a_day_takes_only_its_own_images(self):
        self._upload([_png("event.png")])
        self._upload([_png("day.png")], event_day=self.day)

        self.day.delete()

        self.assertEqual(EventImage.objects.count(), 1)
        self.assertIsNone(EventImage.objects.first().event_day_id)

    def test_deleting_the_event_takes_both_galleries(self):
        self._upload([_png("event.png")])
        self._upload([_png("day.png")], event_day=self.day)

        self.event.delete()

        self.assertEqual(EventImage.objects.count(), 0)

    def test_the_delete_preview_counts_gallery_images(self):
        self._upload([_png("event.png")])
        self._upload([_png("day.png")], event_day=self.day)

        impact = get_event_deletion_impact(self.event)
        # Both galleries cascade from `event`, so one count covers both — the day
        # image must not be tallied twice.
        self.assertEqual(impact["event_images"], 2)


class EventImageStoragePathTests(TestCase):
    """
    The retired single-image fields resolved to a CONSTANT name
    (`covers/cover.jpg`, `days/<id>/images/image.jpg`). Fine for one file; for a
    gallery, every row would resolve to the same path and the storage backend
    would quietly append a random suffix to each, leaving blobs with no stable
    mapping back to a row.
    """

    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="pathclient@example.com", password="x",
        )
        self.event = _make_event(self.client_user)
        self.day = EventDay.objects.create(
            owner=self.event, event_day_title="Day", date=datetime.date(2026, 11, 20),
        )

    def test_each_image_gets_its_own_path_segment(self):
        a = EventImage(event=self.event)
        b = EventImage(event=self.event)
        path_a = event_gallery_upload_path(a, "photo.png")
        path_b = event_gallery_upload_path(b, "photo.png")

        self.assertNotEqual(path_a, path_b)
        self.assertIn(str(a.pk), path_a)
        self.assertIn("photo.png", path_a)  # original filename preserved

    def test_a_day_image_lands_under_its_day(self):
        image = EventImage(event=self.event, event_day=self.day)
        path = event_gallery_upload_path(image, "photo.png")
        self.assertIn(f"days/{self.day.pk}/gallery/", path)

    def test_an_event_image_lands_outside_any_day(self):
        image = EventImage(event=self.event)
        path = event_gallery_upload_path(image, "photo.png")
        self.assertIn("/gallery/", path)
        self.assertNotIn("/days/", path)


@override_settings(USE_R2_STORAGE=False)
class EventImageBlobCleanupTests(TestCase):
    """
    The gap this closes: `Event.featured_image` and `EventDay.event_images` had NO
    post_delete receiver, so deleting an event left its cover in the bucket for
    ever. Survivable at one blob per event; a gallery of dozens per event is not.

    The cascade cases are the point. Django's "fast delete" optimisation skips
    per-row signals when it can delete a table in one statement — registering a
    post_delete receiver is what forces it off for this model, so these assert
    the receiver fires on paths that never touch view code.
    """

    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="blobclient@example.com", password="x",
        )
        self.event = _make_event(self.client_user)
        self.day = EventDay.objects.create(
            owner=self.event, event_day_title="Day", date=datetime.date(2026, 11, 20),
        )

    def _image(self, event_day=None):
        image = EventImage(event=self.event, event_day=event_day)
        image.image.save("photo.png", _png(), save=True)
        return image

    def test_deleting_a_row_deletes_its_blob(self):
        image = self._image()
        name, storage = image.image.name, image.image.storage
        self.assertTrue(storage.exists(name))

        with self.captureOnCommitCallbacks(execute=True):
            image.delete()

        self.assertFalse(storage.exists(name))

    def test_deleting_the_event_deletes_every_blob_in_both_galleries(self):
        event_level = self._image()
        day_level = self._image(event_day=self.day)
        names = [(i.image.name, i.image.storage) for i in (event_level, day_level)]

        with self.captureOnCommitCallbacks(execute=True):
            self.event.delete()

        for name, storage in names:
            self.assertFalse(storage.exists(name), f"{name} survived the cascade")

    def test_deleting_a_day_deletes_its_blobs(self):
        day_level = self._image(event_day=self.day)
        name, storage = day_level.image.name, day_level.image.storage

        with self.captureOnCommitCallbacks(execute=True):
            self.day.delete()

        self.assertFalse(storage.exists(name))

    def test_a_rolled_back_delete_leaves_the_blob_alone(self):
        """Storage is not transactional, so the blob delete is deferred to
        on_commit. If it ran inline, a rollback would restore the row and leave it
        pointing at a file that no longer exists."""
        image = self._image()
        name, storage = image.image.name, image.image.storage

        with self.captureOnCommitCallbacks(execute=False):
            image.delete()

        self.assertTrue(storage.exists(name))


class AdminFormsBuildTests(TestCase):
    """
    Renaming or removing a model field leaves any admin `fieldsets` entry naming
    it stale, and `manage.py check` does NOT catch it — Django can't tell an
    unknown field from a callable or a readonly attribute at check time, so it
    passes and then raises FieldError when the change page builds its form. This
    change removed two fields and hit exactly that: EventAdmin kept
    `featured_image` in a fieldset and `check` reported no issues.

    Building the form is what raises, so that is what this does.
    """

    def test_every_events_admin_form_builds(self):
        for model in (Event, EventDay, EventImage):
            with self.subTest(model=model.__name__):
                admin.site._registry[model].get_form(None)()

    def test_the_inlines_build_too(self):
        """Inlines have their own `fields` and are missed by the check above.

        A real request is needed here, not None: get_formset consults
        has_delete_permission, which reads request.user.
        """
        superuser = User.objects.create_superuser(
            email="adminforms@example.com", password="x",
            first_name="Root", last_name="User",
        )
        request = factory.get("/")
        request.user = superuser

        for model in (Event, EventDay):
            model_admin = admin.site._registry[model]
            for inline_cls in model_admin.inlines:
                with self.subTest(inline=inline_cls.__name__):
                    inline = inline_cls(model_admin.model, admin.site)
                    inline.get_formset(request)
