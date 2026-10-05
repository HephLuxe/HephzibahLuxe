"""
apps/events/test_published_lock.py

Once an event is published, a client can no longer change what its public page
shows. Structural actions (add/delete a day, reorder or delete an image) are a
403 with code `event_published`; field writes that reach the page
(`event_day_title`, an image's `alt_text` / `sort_order` / `is_primary`) are
ignored, like the staff-only fields in test_editorial_lock.py.

Every path is shown three ways: client on a published event (blocked or
ignored), client on an unpublished event (allowed, as before), staff on a
published event (allowed).
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.error_codes import EVENT_PUBLISHED
from apps.events import views
from apps.events.models import EventDay, EventImage
from apps.events.tests import _make_event, _png

User = get_user_model()
factory = APIRequestFactory()


class PublishedEventLockTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            first_name="Win", last_name="Team", email="publockstaff@example.com",
            password="x", role="staff",
        )
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="publockclient@example.com", password="x",
        )
        self.event = _make_event(self.client_user)
        self.day = EventDay.objects.create(
            owner=self.event, event_day_title="Event No. 1", date=datetime.date(2027, 6, 1),
        )
        self.first = EventImage.objects.create(
            event=self.event, image=_png("a.png"), alt_text="A", is_primary=True, sort_order=0,
        )
        self.second = EventImage.objects.create(
            event=self.event, image=_png("b.png"), alt_text="B", sort_order=1,
        )

    def _publish(self):
        self.event.is_published = True
        self.event.save()

    def _as(self, user, req):
        force_authenticate(req, user=user)
        return req

    def _assert_published_403(self, resp):
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data["code"], EVENT_PUBLISHED)
        self.assertIn("live on the public portfolio", resp.data["detail"])

    # ── create an event day ──────────────────────────────────────────────────

    def _create_day(self, user):
        req = self._as(user, factory.post("/", {"date": "2027-06-02", "event_day_title": "New"}, format="json"))
        return views.create_eventday(req, event_slug=self.event.slug)

    def test_create_day_client_published_is_403(self):
        self._publish()
        self._assert_published_403(self._create_day(self.client_user))
        self.assertEqual(self.event.days.count(), 1)

    def test_create_day_client_unpublished_is_allowed(self):
        self.assertEqual(self._create_day(self.client_user).status_code, 201)
        self.assertEqual(self.event.days.count(), 2)

    def test_create_day_staff_published_is_allowed(self):
        self._publish()
        self.assertEqual(self._create_day(self.staff).status_code, 201)

    # ── delete an event day (staff-only already; pinned for published) ───────

    def _delete_day(self, user):
        req = self._as(user, factory.delete("/"))
        return views.delete_eventday(req, event_slug=self.event.slug, id=self.day.id)

    def test_delete_day_client_published_is_403(self):
        self._publish()
        self.assertEqual(self._delete_day(self.client_user).status_code, 403)
        self.assertTrue(EventDay.objects.filter(pk=self.day.pk).exists())

    def test_delete_day_staff_published_is_allowed(self):
        self._publish()
        self.assertEqual(self._delete_day(self.staff).status_code, 200)
        self.assertFalse(EventDay.objects.filter(pk=self.day.pk).exists())

    # ── update an event day: event_day_title ─────────────────────────────────

    def _patch_day(self, user, body):
        req = self._as(user, factory.patch("/", body, format="json"))
        return views.update_eventday(req, event_slug=self.event.slug, id=self.day.id)

    def test_day_title_client_published_is_ignored_other_fields_save(self):
        self._publish()
        resp = self._patch_day(self.client_user, {"event_day_title": "Renamed", "dress_code": "Gold"})
        self.assertEqual(resp.status_code, 200)
        self.day.refresh_from_db()
        self.assertEqual(self.day.event_day_title, "Event No. 1")
        self.assertEqual(self.day.dress_code, "Gold")

    def test_day_title_client_unpublished_is_applied(self):
        self._patch_day(self.client_user, {"event_day_title": "Renamed"})
        self.day.refresh_from_db()
        self.assertEqual(self.day.event_day_title, "Renamed")

    def test_day_title_staff_published_is_applied(self):
        self._publish()
        self._patch_day(self.staff, {"event_day_title": "Renamed"})
        self.day.refresh_from_db()
        self.assertEqual(self.day.event_day_title, "Renamed")

    def test_day_put_round_trip_by_client_on_published_is_not_refused(self):
        self._publish()
        body = {"date": "2027-06-01", "event_day_title": "Renamed"}
        req = self._as(self.client_user, factory.put("/", body, format="json"))
        resp = views.update_eventday(req, event_slug=self.event.slug, id=self.day.id)
        self.assertEqual(resp.status_code, 200)
        self.day.refresh_from_db()
        self.assertEqual(self.day.event_day_title, "Event No. 1")

    # ── PATCH an image: alt_text, sort_order, is_primary ─────────────────────

    def _patch_image(self, user, image, body):
        req = self._as(user, factory.patch("/", body, format="json"))
        return views.event_gallery_image(req, event_slug=self.event.slug, image_id=image.id)

    def _image_state(self):
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        return (self.second.alt_text, self.second.sort_order, self.second.is_primary, self.first.is_primary)

    def test_image_fields_client_published_are_ignored(self):
        self._publish()
        resp = self._patch_image(self.client_user, self.second, {
            "alt_text": "Changed", "sort_order": 9, "is_primary": True,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._image_state(), ("B", 1, False, True))

    def test_image_fields_client_unpublished_are_applied(self):
        self._patch_image(self.client_user, self.second, {
            "alt_text": "Changed", "sort_order": 9, "is_primary": True,
        })
        self.assertEqual(self._image_state(), ("Changed", 9, True, False))

    def test_image_fields_staff_published_are_applied(self):
        self._publish()
        self._patch_image(self.staff, self.second, {
            "alt_text": "Changed", "sort_order": 9, "is_primary": True,
        })
        self.assertEqual(self._image_state(), ("Changed", 9, True, False))

    # ── reorder ──────────────────────────────────────────────────────────────

    def _reorder(self, user):
        body = {"image_ids": [str(self.second.id), str(self.first.id)]}
        req = self._as(user, factory.post("/", body, format="json"))
        return views.reorder_event_gallery(req, event_slug=self.event.slug)

    def test_reorder_client_published_is_403(self):
        self._publish()
        self._assert_published_403(self._reorder(self.client_user))
        self.second.refresh_from_db()
        self.assertEqual(self.second.sort_order, 1)

    def test_reorder_client_unpublished_is_allowed(self):
        self.assertEqual(self._reorder(self.client_user).status_code, 200)
        self.second.refresh_from_db()
        self.assertEqual(self.second.sort_order, 0)

    def test_reorder_staff_published_is_allowed(self):
        self._publish()
        self.assertEqual(self._reorder(self.staff).status_code, 200)

    # ── delete an image ──────────────────────────────────────────────────────

    def _delete_image(self, user):
        req = self._as(user, factory.delete("/"))
        return views.event_gallery_image(req, event_slug=self.event.slug, image_id=self.second.id)

    def test_delete_image_client_published_is_403(self):
        self._publish()
        self._assert_published_403(self._delete_image(self.client_user))
        self.assertTrue(EventImage.objects.filter(pk=self.second.pk).exists())

    def test_delete_image_client_unpublished_is_allowed(self):
        self.assertEqual(self._delete_image(self.client_user).status_code, 200)
        self.assertFalse(EventImage.objects.filter(pk=self.second.pk).exists())

    def test_delete_image_staff_published_is_allowed(self):
        self._publish()
        self.assertEqual(self._delete_image(self.staff).status_code, 200)

    # ── upload: still allowed on a published event, starts unpublished ───────

    def test_upload_client_published_is_allowed_and_unpublished(self):
        self._publish()
        req = self._as(self.client_user, factory.post(
            "/", {"image": [_png("c.png")], "alt_text": "New one"}, format="multipart",
        ))
        resp = views.event_gallery(req, event_slug=self.event.slug)
        self.assertEqual(resp.status_code, 201)
        created = EventImage.objects.get(pk=resp.data[0]["id"])
        self.assertFalse(created.is_published)
        self.assertEqual(created.alt_text, "New one")
