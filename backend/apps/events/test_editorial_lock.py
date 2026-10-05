"""
apps/events/test_editorial_lock.py

The public-portfolio fields are staff-only. A client may edit their own event,
its days and its gallery through the portal API, and before this they could
also publish the event or rewrite its public copy with the same PATCH. For a
non-staff caller those fields are now read-only: accepted in the body, ignored
(DRF read-only semantics), so a portal PUT that round-trips the GET response
keeps working.

Each client case below has a staff twin, so the lock is shown to be scoped to
clients rather than to have removed the capability.
"""

import datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.events import public_views, views
from apps.events.models import Event, EventDay, EventImage
from apps.events.tests import _make_event, _png

User = get_user_model()
factory = APIRequestFactory()


@override_settings(USE_R2_STORAGE=False)
class EditorialFieldsAreStaffOnlyTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            first_name="Win", last_name="Team", email="lockstaff@example.com",
            password="x", role="staff",
        )
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="lockclient@example.com", password="x",
        )
        self.event = _make_event(self.client_user)
        self.event.headline = "Staff Headline"
        self.event.description = "Staff narrative."
        self.event.save()
        self.day = EventDay.objects.create(
            owner=self.event, event_day_title="Event No. 1", headline="Staff day headline",
            content="Staff day story.", date=datetime.date(2027, 6, 1),
        )
        cache.clear()

    def _as(self, user, req):
        force_authenticate(req, user=user)
        return req

    def _patch_event(self, user, body):
        return views.update_event(self._as(user, factory.patch("/", body, format="json")), slug=self.event.slug)

    def _public_list(self):
        return public_views.portfolio_events(factory.get("/")).data

    # ── Event ────────────────────────────────────────────────────────────────

    def test_a_client_cannot_publish_their_event(self):
        resp = self._patch_event(self.client_user, {"is_published": True, "state": "Ogun"})

        self.assertEqual(resp.status_code, 200)
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_published)
        self.assertEqual(self.event.state, "Ogun")  # the ordinary field still saved
        self.assertFalse(resp.data["is_published"])
        self.assertEqual(self._public_list(), [])

    def test_a_client_cannot_rewrite_the_public_copy_or_slug(self):
        original_slug = self.event.public_slug
        self._patch_event(self.client_user, {
            "headline": "Hacked", "description": "Hacked.", "public_slug": "hacked",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.headline, "Staff Headline")
        self.assertEqual(self.event.description, "Staff narrative.")
        self.assertEqual(self.event.public_slug, original_slug)

    def test_a_client_put_that_round_trips_the_get_response_is_not_refused(self):
        """Ignored, not 400: the portal sends whole objects back."""
        body = {
            "title": self.event.title, "country": "NG", "state": "Lagos",
            "event_date": "2027-06-01", "is_published": True, "headline": "X",
        }
        req = self._as(self.client_user, factory.put("/", body, format="json"))
        resp = views.update_event(req, slug=self.event.slug)
        self.assertEqual(resp.status_code, 200)
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_published)
        self.assertEqual(self.event.headline, "Staff Headline")

    def test_staff_can_publish_and_edit_the_public_fields(self):
        resp = self._patch_event(self.staff, {
            "is_published": True, "headline": "New", "description": "New.",
            "public_slug": "golden-50th",
        })
        self.assertEqual(resp.status_code, 200)
        self.event.refresh_from_db()
        self.assertTrue(self.event.is_published)
        self.assertEqual(
            (self.event.headline, self.event.description, self.event.public_slug),
            ("New", "New.", "golden-50th"),
        )
        self.assertEqual([e["slug"] for e in self._public_list()], ["golden-50th"])

    def test_staff_public_slug_must_be_lowercase_and_unique(self):
        bad = self._patch_event(self.staff, {"public_slug": "Golden-50th"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("public_slug", bad.data["errors"])

        other = _make_event(self.client_user, title="Other")
        other.public_slug = "taken"
        other.save()
        dupe = self._patch_event(self.staff, {"public_slug": "taken"})
        self.assertEqual(dupe.status_code, 400)
        self.assertIn("public_slug", dupe.data["errors"])

    def test_a_serializer_without_a_request_is_locked_down(self):
        """No request in context counts as not staff — fail closed."""
        from apps.events.serializers import EventSerializer

        s = EventSerializer(self.event, data={"is_published": True}, partial=True)
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_published)

    # ── EventDay ─────────────────────────────────────────────────────────────

    def _patch_day(self, user, body):
        req = self._as(user, factory.patch("/", body, format="json"))
        return views.update_eventday(req, event_slug=self.event.slug, id=self.day.id)

    def test_a_client_cannot_edit_a_days_public_copy_or_slug(self):
        resp = self._patch_day(self.client_user, {
            "headline": "Hacked", "content": "Hacked.", "slug": "hacked", "dress_code": "White",
        })
        self.assertEqual(resp.status_code, 200)
        self.day.refresh_from_db()
        self.assertEqual(self.day.headline, "Staff day headline")
        self.assertEqual(self.day.content, "Staff day story.")
        self.assertEqual(self.day.slug, "event-no-1")
        self.assertEqual(self.day.dress_code, "White")

    def test_a_client_creating_a_day_cannot_set_its_public_copy(self):
        req = self._as(self.client_user, factory.post("/", {
            "event_day_title": "Afterparty", "headline": "Hacked", "content": "Hacked.",
            "slug": "hacked", "date": "2027-06-02",
        }, format="json"))
        resp = views.create_eventday(req, event_slug=self.event.slug)
        self.assertEqual(resp.status_code, 201)
        day = EventDay.objects.get(id=resp.data["id"])
        self.assertEqual((day.headline, day.content), ("", None))
        self.assertEqual(day.slug, "afterparty")  # generated, not the client's value

    def test_staff_can_edit_a_days_public_copy_and_slug(self):
        resp = self._patch_day(self.staff, {"headline": "H", "content": "C", "slug": "celebration-night"})
        self.assertEqual(resp.status_code, 200)
        self.day.refresh_from_db()
        self.assertEqual((self.day.headline, self.day.content, self.day.slug), ("H", "C", "celebration-night"))

    def test_staff_day_slug_must_be_unique_within_the_event(self):
        EventDay.objects.create(owner=self.event, slug="taken", date=datetime.date(2027, 6, 2))
        resp = self._patch_day(self.staff, {"slug": "taken"})
        self.assertEqual(resp.status_code, 400)

    # ── Gallery ──────────────────────────────────────────────────────────────

    def _upload(self, user):
        req = self._as(user, factory.post("/", {"image": [_png("p.png")]}, format="multipart"))
        return views.event_gallery(req, event_slug=self.event.slug)

    def test_a_client_upload_starts_unpublished(self):
        resp = self._upload(self.client_user)
        self.assertEqual(resp.status_code, 201)
        self.assertFalse(resp.data[0]["is_published"])
        self.assertFalse(EventImage.objects.get(id=resp.data[0]["id"]).is_published)

    def test_a_staff_upload_keeps_the_published_default(self):
        resp = self._upload(self.staff)
        self.assertTrue(EventImage.objects.get(id=resp.data[0]["id"]).is_published)

    def _patch_image(self, user, image, body):
        req = self._as(user, factory.patch("/", body, format="json"))
        return views.event_gallery_image(req, event_slug=self.event.slug, image_id=image.id)

    def test_a_client_cannot_publish_an_image(self):
        image = EventImage.objects.get(id=self._upload(self.client_user).data[0]["id"])
        resp = self._patch_image(self.client_user, image, {"is_published": True, "alt_text": "Mine"})
        self.assertEqual(resp.status_code, 200)
        image.refresh_from_db()
        self.assertFalse(image.is_published)
        self.assertEqual(image.alt_text, "Mine")

    def test_staff_can_publish_and_unpublish_an_image(self):
        image = EventImage.objects.get(id=self._upload(self.client_user).data[0]["id"])
        self._patch_image(self.staff, image, {"is_published": True})
        image.refresh_from_db()
        self.assertTrue(image.is_published)
        self._patch_image(self.staff, image, {"is_published": False})
        image.refresh_from_db()
        self.assertFalse(image.is_published)

    def test_a_client_upload_to_a_published_event_does_not_appear_publicly(self):
        self.event.is_published = True
        self.event.save()
        self._upload(self.client_user)
        cache.clear()
        detail = public_views.portfolio_event_detail(factory.get("/"), slug=self.event.public_slug).data
        self.assertIsNone(detail["cover_image"])
        self.assertEqual(detail["images"], [])


class PublicSlugGenerationTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="slugclient@example.com", password="x",
        )

    def _event(self, title="Winifred Ojulari's Birthday", headline=""):
        return Event.objects.create(
            celebrant=self.client_user, title=title, headline=headline, country="NG",
            state="Lagos", event_date=datetime.date(2021, 1, 1),
        )

    def test_generated_from_the_headline_never_the_title(self):
        event = self._event(headline="A Golden 50th")
        self.assertEqual(event.public_slug, "a-golden-50th")
        self.assertNotIn("winifred", event.public_slug)
        self.assertEqual(event.slug, "winifred-ojularis-birthday")  # internal slug unchanged

    def test_no_headline_no_public_slug(self):
        self.assertIsNone(self._event().public_slug)

    def test_blank_is_stored_as_null_so_blanks_do_not_collide(self):
        a, b = self._event(title="A"), self._event(title="B")
        a.public_slug = ""
        a.save()
        b.public_slug = ""
        b.save()
        a.refresh_from_db()
        self.assertIsNone(a.public_slug)

    def test_collisions_get_numbered_suffixes_in_one_query(self):
        self._event(title="One", headline="Golden")
        self._event(title="Two", headline="Golden")
        third = Event(
            celebrant=self.client_user, title="Three", headline="Golden",
            country="NG", state="Lagos", event_date=datetime.date(2021, 1, 1),
        )
        # internal slug lookup (1) + public slug lookup (1) + INSERT (1)
        with self.assertNumQueries(3):
            third.save()
        self.assertEqual(third.public_slug, "golden-3")

    def test_set_once_then_left_alone_when_the_headline_changes(self):
        event = self._event(headline="First")
        event.headline = "Second"
        event.save()
        event.refresh_from_db()
        self.assertEqual(event.public_slug, "first")

    def test_a_headline_added_later_generates_it_even_with_update_fields(self):
        event = self._event()
        event.headline = "Later"
        event.save(update_fields=["headline"])
        event.refresh_from_db()
        self.assertEqual(event.public_slug, "later")

    def test_day_slugs_are_unique_per_event_not_globally(self):
        e1 = self._event(title="E1")
        e2 = self._event(title="E2")
        d1 = EventDay.objects.create(owner=e1, event_day_title="Event No. 1", date=datetime.date(2021, 1, 1))
        d2 = EventDay.objects.create(owner=e1, event_day_title="Event No. 1", date=datetime.date(2021, 1, 2))
        d3 = EventDay.objects.create(owner=e2, event_day_title="Event No. 1", date=datetime.date(2021, 1, 1))
        self.assertEqual((d1.slug, d2.slug, d3.slug), ("event-no-1", "event-no-1-2", "event-no-1"))

    def test_day_slug_falls_back_to_the_headline(self):
        day = EventDay.objects.create(
            owner=self._event(), headline="Rooted in Gratitude", date=datetime.date(2021, 1, 1),
        )
        self.assertEqual(day.slug, "rooted-in-gratitude")

    def test_underscores_become_hyphens_and_the_slug_passes_full_clean(self):
        """slugify() keeps "_" but lowercase_slug_validator rejects it, so an
        underscore headline used to produce a slug the next admin save refused."""
        event = self._event(headline="Ade_Bola _ Forever")
        event.event_venue = "Eko Hotel"  # required by full_clean, unrelated to slugs
        event.save()
        self.assertEqual(event.public_slug, "ade-bola-forever")
        day = EventDay.objects.create(
            owner=event, event_day_title="Day_One", date=datetime.date(2021, 1, 1),
        )
        self.assertEqual(day.slug, "day-one")

        event.refresh_from_db()
        event.full_clean()  # raises if the stored slug fails the validator
        event.save()
        day.refresh_from_db()
        day.full_clean()
        day.save()


class PublicApiUsesPublicSlugTests(TestCase):
    def setUp(self):
        self.event = Event.objects.create(
            title="Winifred Ojulari's Birthday", headline="A Golden 50th", country="NG",
            state="Lagos", event_date=datetime.date(2021, 1, 1), is_published=True,
            public_slug="golden-50th",
        )
        EventDay.objects.create(
            owner=self.event, event_day_title="Pre-Birthday Photoshoot",
            date=datetime.date(2021, 1, 1), slug="pre-birthday-photoshoot",
        )
        cache.clear()

    def _detail(self, slug):
        return public_views.portfolio_event_detail(factory.get("/"), slug=slug)

    def test_list_slug_key_carries_the_public_slug(self):
        data = public_views.portfolio_events(factory.get("/")).data
        self.assertEqual([e["slug"] for e in data], ["golden-50th"])

    def test_detail_by_public_slug_with_day_slugs(self):
        resp = self._detail("golden-50th")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["slug"], "golden-50th")
        self.assertEqual([d["slug"] for d in resp.data["event_days"]], ["pre-birthday-photoshoot"])

    def test_the_internal_slug_does_not_resolve(self):
        self.assertEqual(self.event.slug, "winifred-ojularis-birthday")
        self.assertEqual(self._detail(self.event.slug).status_code, 404)

    def test_a_published_event_without_a_public_slug_is_not_listed_and_is_logged(self):
        Event.objects.create(
            title="No Headline", country="NG", state="Lagos",
            event_date=datetime.date(2022, 1, 1), is_published=True,
        )
        with self.assertLogs("apps.events.public_views", level="WARNING") as logs:
            data = public_views.portfolio_events(factory.get("/")).data
        self.assertEqual([e["slug"] for e in data], ["golden-50th"])
        self.assertIn("without a public_slug", logs.output[0])

    def test_renaming_the_public_slug_drops_the_old_cached_detail(self):
        self.assertEqual(self._detail("golden-50th").status_code, 200)  # prime cache
        event = Event.objects.get(pk=self.event.pk)
        with self.captureOnCommitCallbacks(execute=True):
            event.public_slug = "golden-fifty"
            event.save()
        self.assertEqual(self._detail("golden-50th").status_code, 404)
        self.assertEqual(self._detail("golden-fifty").status_code, 200)


class AdminBulkActionsInvalidateCacheTests(TestCase):
    """queryset.update() fires no post_save, so the admin's bulk publish actions
    clear the cache by hand — the index AND every affected detail key."""

    def setUp(self):
        from django.contrib import admin

        self.superuser = User.objects.create_superuser(
            email="bulkadmin@example.com", password="x", first_name="Root", last_name="User",
        )
        self.event = Event.objects.create(
            title="Bulk", headline="Bulk Story", country="NG", state="Lagos",
            event_date=datetime.date(2021, 1, 1), is_published=True,
        )
        self.image = EventImage.objects.create(event=self.event, image="g/a.jpg", is_primary=True)
        self.event_admin = admin.site._registry[Event]
        self.image_admin = admin.site._registry[EventImage]
        cache.clear()

    def _request(self):
        from django.contrib.messages.storage.fallback import FallbackStorage

        req = factory.post("/")
        req.user = self.superuser
        req.session = {}
        req._messages = FallbackStorage(req)
        return req

    def _detail(self):
        return public_views.portfolio_event_detail(factory.get("/"), slug=self.event.public_slug)

    def test_unpublish_action_drops_the_cached_detail_and_list(self):
        self.assertEqual(self._detail().status_code, 200)
        self.assertEqual(len(public_views.portfolio_events(factory.get("/")).data), 1)

        with self.captureOnCommitCallbacks(execute=True):
            self.event_admin.unpublish_events(self._request(), Event.objects.filter(pk=self.event.pk))

        self.assertEqual(self._detail().status_code, 404)
        self.assertEqual(public_views.portfolio_events(factory.get("/")).data, [])

    def test_publish_action_drops_a_cached_404_free_detail(self):
        Event.objects.filter(pk=self.event.pk).update(is_published=False)
        cache.clear()
        self.assertEqual(public_views.portfolio_events(factory.get("/")).data, [])

        with self.captureOnCommitCallbacks(execute=True):
            self.event_admin.publish_events(self._request(), Event.objects.filter(pk=self.event.pk))

        self.assertEqual(len(public_views.portfolio_events(factory.get("/")).data), 1)
        self.assertEqual(self._detail().status_code, 200)

    def test_image_hide_action_drops_the_cached_detail(self):
        EventImage.objects.create(event=self.event, image="g/b.jpg", sort_order=1)
        self.assertEqual(len(self._detail().data["images"]), 1)

        with self.captureOnCommitCallbacks(execute=True):
            self.image_admin.unpublish_images(
                self._request(), EventImage.objects.filter(image="g/b.jpg"),
            )

        self.assertEqual(self._detail().data["images"], [])


class StaffDaySlugOnCreateTests(TestCase):
    def test_a_duplicate_day_slug_on_create_is_a_400(self):
        staff = User.objects.create_user(
            first_name="Win", last_name="Team", email="dupstaff@example.com", password="x", role="staff",
        )
        event = Event.objects.create(
            title="E", country="NG", state="Lagos", event_date=datetime.date(2021, 1, 1),
        )
        EventDay.objects.create(owner=event, slug="taken", date=datetime.date(2021, 1, 1))
        req = factory.post("/", {"slug": "taken", "date": "2021-01-02"}, format="json")
        force_authenticate(req, user=staff)
        resp = views.create_eventday(req, event_slug=event.slug)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("slug", resp.data["errors"])
