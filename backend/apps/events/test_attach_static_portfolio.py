"""
apps/events/test_attach_static_portfolio.py

attach_static_portfolio maps a manifest event onto an EXISTING event. The
fixtures mimic the three production events: client-name internal slugs,
unpublished, a 3-day birthday with day images but no event-level cover, a 1-day
birthday with a cover, and a corporate event with no images at all.
"""

import datetime
import json
import shutil
import tempfile
import uuid
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.storage import InMemoryStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIRequestFactory

from apps.events import public_views
from apps.events.management.commands.import_static_portfolio import DEFAULT_MANIFEST
from apps.events.models import Event, EventDay, EventImage
from apps.events.test_import_static_portfolio import MANIFEST
from apps.notifications.models import Notification

User = get_user_model()
factory = APIRequestFactory()


@override_settings(USE_R2_STORAGE=False)
class AttachStaticPortfolioTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.images_root = self.tmp / "public"
        (self.images_root / "images" / "p").mkdir(parents=True)
        for name in ("cover", "day1", "a", "b", "day2", "forum-cover", "f1", "f2"):
            Image.new("RGB", (2, 2), "white").save(self.images_root / "images" / "p" / f"{name}.jpg")
        self.manifest = self.tmp / "manifest.json"
        self.manifest.write_text(json.dumps(MANIFEST))

        self.client_user = User.objects.create_user(
            first_name="Winifred", last_name="Ojulari", email="winifred@example.com", password="x",
        )
        # golden-test shape: headline == static title, 2 days, day images, NO event cover.
        self.golden = Event.objects.create(
            celebrant=self.client_user, title="Winifred Ojulari's Birthday",
            headline="A Golden Test: Two Days", event_type="Birthday",
            country="Nigeria", state="Lagos", event_date=datetime.date(2021, 1, 1),
        )
        self.g_day1 = EventDay.objects.create(
            owner=self.golden, event_day_title="Pre-Birthday Photoshoot", date=datetime.date(2021, 1, 1),
        )
        self.g_day2 = EventDay.objects.create(
            owner=self.golden, event_day_title="Event No. 1", date=datetime.date(2021, 1, 2),
        )
        # Already uploaded by staff under the same file name: must not be duplicated.
        EventImage.objects.create(
            event=self.golden, event_day=self.g_day1, is_primary=True,
            image=f"portals/x/events/{self.golden.pk}/days/{self.g_day1.pk}/gallery/1/day1.jpg",
        )
        # msme shape: no images, no days.
        self.forum = Event.objects.create(
            celebrant=self.client_user, title="MSME Engagement", headline="A Forum",
            event_type="Corporate", country="Nigeria", state="Lagos",
            event_date=datetime.date(2026, 1, 1),
        )
        cache.clear()

    def _run(self, static, event, *flags):
        out = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command(
                "attach_static_portfolio", "--static-slug", static, "--event", event.slug,
                "--manifest", str(self.manifest), "--images-root", str(self.images_root),
                *flags, stdout=out,
            )
        return out.getvalue()

    def _counts(self):
        return (
            list(Event.objects.order_by("pk").values_list("public_slug", "is_published")),
            list(EventDay.objects.order_by("pk").values_list("slug", flat=True)),
            EventImage.objects.count(),
        )

    ALL = ("--set-public-slug", "--day-slugs", "--cover", "--gallery", "--publish")

    # ── dry run ──────────────────────────────────────────────────────────────

    def test_dry_run_prints_the_plan_and_writes_nothing(self):
        before = self._counts()
        with mock.patch.object(InMemoryStorage, "save", side_effect=AssertionError("upload")):
            out = self._run("golden-test", self.golden, *self.ALL)
        self.assertEqual(self._counts(), before)
        self.assertIn("DRY RUN", out)
        self.assertIn("SET public_slug 'a-golden-test-two-days' -> 'golden-test'", out)
        self.assertIn("-> 'pre-birthday'", out)
        self.assertIn("UPLOAD cover /images/p/cover.jpg -> event level, is_primary=True, sort_order=0", out)
        # day 1 already has an image, so its gallery is skipped whole.
        self.assertIn("SKIP day 'Pre-Birthday Photoshoot': already has 1 image", out)
        self.assertIn("UPLOAD /images/p/day2.jpg -> day", out)
        self.assertNotIn("UPLOAD /images/p/a.jpg", out)
        self.assertNotIn("WARNING", out)
        self.assertIn("SET is_published False -> True", out)
        self.assertIn("DAY MAPPING (by title", out)

    def test_no_flags_is_an_error(self):
        with self.assertRaises(CommandError):
            self._run("golden-test", self.golden)

    def test_unknown_static_slug_or_event_is_an_error(self):
        with self.assertRaises(CommandError):
            self._run("nope", self.golden, "--publish")
        ghost = Event(slug="ghost")
        with self.assertRaises(CommandError):
            self._run("golden-test", ghost, "--publish")

    # ── each step ────────────────────────────────────────────────────────────

    def test_set_public_slug_leaves_the_internal_slug_alone(self):
        internal = self.golden.slug
        self._run("golden-test", self.golden, "--set-public-slug", "--commit")
        self.golden.refresh_from_db()
        self.assertEqual(self.golden.public_slug, "golden-test")
        self.assertEqual(self.golden.slug, internal)

    def test_set_public_slug_refuses_a_slug_held_by_another_event(self):
        self.forum.public_slug = "golden-test"
        self.forum.save()
        with self.assertRaises(CommandError):
            self._run("golden-test", self.golden, "--set-public-slug", "--commit")
        self.golden.refresh_from_db()
        self.assertNotEqual(self.golden.public_slug, "golden-test")

    def test_day_slugs_by_title(self):
        self._run("golden-test", self.golden, "--day-slugs", "--commit")
        self.g_day1.refresh_from_db()
        self.g_day2.refresh_from_db()
        self.assertEqual((self.g_day1.slug, self.g_day2.slug), ("pre-birthday", "rooted"))

    def test_day_slugs_by_order_when_titles_do_not_match(self):
        EventDay.objects.filter(pk=self.g_day1.pk).update(event_day_title="Shoot")
        EventDay.objects.filter(pk=self.g_day2.pk).update(event_day_title="Party")
        out = self._run("golden-test", self.golden, "--day-slugs", "--commit")
        self.assertIn("by order", out)
        self.g_day1.refresh_from_db()
        self.assertEqual(self.g_day1.slug, "pre-birthday")

    def test_day_slugs_refuse_when_order_and_title_disagree(self):
        # The first-dated day carries the SECOND sub-event's title.
        EventDay.objects.filter(pk=self.g_day1.pk).update(event_day_title="Event No. 1")
        EventDay.objects.filter(pk=self.g_day2.pk).update(event_day_title="Something")
        with self.assertRaises(CommandError):
            self._run("golden-test", self.golden, "--day-slugs", "--commit")

    def test_day_slugs_refuse_when_counts_differ_and_titles_do_not_pair(self):
        EventDay.objects.filter(pk=self.g_day2.pk).update(event_day_title="Party")
        EventDay.objects.create(owner=self.golden, event_day_title="Extra", date=datetime.date(2021, 1, 3))
        with self.assertRaises(CommandError):
            self._run("golden-test", self.golden, "--day-slugs", "--commit")

    def test_day_slugs_can_swap_without_tripping_the_unique_constraint(self):
        EventDay.objects.filter(pk=self.g_day1.pk).update(slug="rooted")
        EventDay.objects.filter(pk=self.g_day2.pk).update(slug="pre-birthday")
        self._run("golden-test", self.golden, "--day-slugs", "--commit")
        self.g_day1.refresh_from_db()
        self.assertEqual(self.g_day1.slug, "pre-birthday")

    def test_cover_only_when_there_is_no_event_level_primary(self):
        self._run("golden-test", self.golden, "--cover", "--commit")
        cover = self.golden.images.get(event_day__isnull=True)
        self.assertTrue(cover.is_primary)
        self.assertTrue(cover.image.name.endswith("/cover.jpg"))
        self.assertIn(f"/events/{self.golden.pk}/gallery/", cover.image.name)

        out = self._run("golden-test", self.golden, "--cover", "--commit")
        self.assertIn("already has an event-level primary", out)
        self.assertEqual(self.golden.images.filter(event_day__isnull=True).count(), 1)

    def test_gallery_only_fills_empty_targets(self):
        out = self._run("golden-test", self.golden, "--gallery", "--commit")
        self.assertIn("SKIP day 'Pre-Birthday Photoshoot': already has 1 image", out)
        day1 = [i.image.name.rsplit("/", 1)[1] for i in self.g_day1.images.all()]
        day2 = [i.image.name.rsplit("/", 1)[1] for i in self.g_day2.images.all()]
        self.assertEqual(day1, ["day1.jpg"])  # untouched: it was not empty
        self.assertEqual(day2, ["day2.jpg"])
        self.assertTrue(self.g_day2.images.get().is_primary)  # day had no cover
        self.assertEqual(self.g_day1.images.filter(is_primary=True).count(), 1)
        # Gallery without --cover does not add an event-level image for a multi-day event.
        self.assertEqual(self.golden.images.filter(event_day__isnull=True).count(), 0)

    def test_force_gallery_fills_non_empty_targets_with_name_dedupe_and_warns(self):
        dry = self._run("golden-test", self.golden, "--force-gallery")
        self.assertIn("WARNING: --force-gallery", dry)
        self.assertIn("WILL BE DUPLICATED", dry)
        self.assertIn("day1.jpg already on the event — skip", dry)  # the secondary guard

        self._run("golden-test", self.golden, "--force-gallery", "--commit")
        day1 = sorted(i.image.name.rsplit("/", 1)[1] for i in self.g_day1.images.all())
        self.assertEqual(day1, ["a.jpg", "b.jpg", "day1.jpg"])  # day1.jpg not re-uploaded

    def test_event_level_gallery_is_skipped_when_it_has_images(self):
        EventImage.objects.create(event=self.forum, image="portals/x/events/1/gallery/1/0a1b.jpg", is_primary=True)
        out = self._run("forum-test", self.forum, "--gallery", "--commit")
        self.assertIn("SKIP event level: already has 1 image", out)
        self.assertEqual(self.forum.images.count(), 1)

    def test_single_day_manifest_gallery_goes_to_event_level(self):
        self._run("forum-test", self.forum, "--cover", "--gallery", "--commit")
        names = sorted(i.image.name.rsplit("/", 1)[1] for i in self.forum.images.all())
        self.assertEqual(names, ["f1.jpg", "f2.jpg", "forum-cover.jpg"])
        self.assertEqual(self.forum.images.filter(is_primary=True).count(), 1)
        # Cover first, gallery after it — no shared sort_order.
        ordered = [(i.image.name.rsplit("/", 1)[1], i.sort_order) for i in self.forum.images.all()]
        self.assertEqual(ordered, [("forum-cover.jpg", 0), ("f1.jpg", 1), ("f2.jpg", 2)])

    def test_publish_refuses_without_a_public_slug(self):
        self.forum.headline = ""
        self.forum.public_slug = None
        self.forum.save()
        Event.objects.filter(pk=self.forum.pk).update(public_slug=None)
        with self.assertRaises(CommandError):
            self._run("forum-test", self.forum, "--publish", "--commit")
        self.forum.refresh_from_db()
        self.assertFalse(self.forum.is_published)

    # ── the whole flow ───────────────────────────────────────────────────────

    def test_full_flow_is_public_idempotent_and_silent(self):
        self._run("golden-test", self.golden, *self.ALL, "--commit")
        self._run("forum-test", self.forum, *self.ALL, "--commit")
        after = self._counts()

        listing = public_views.portfolio_events(factory.get("/")).data
        self.assertEqual(sorted(e["slug"] for e in listing), ["forum-test", "golden-test"])
        detail = public_views.portfolio_event_detail(factory.get("/"), slug="golden-test").data
        self.assertEqual([d["slug"] for d in detail["event_days"]], ["pre-birthday", "rooted"])
        self.assertIsNotNone(detail["cover_image"])
        self.assertEqual(
            public_views.portfolio_event_detail(factory.get("/"), slug=self.golden.slug).status_code, 404,
        )

        out = self._run("golden-test", self.golden, *self.ALL, "--commit")
        self.assertIn("(nothing to do)", out)
        self.assertEqual(self._counts(), after)
        self.assertEqual(Notification.objects.count(), 0)


def _uuid_images(event, day, n, primary=True):
    """Images named the way prod stores them — UUID basenames — so nothing in
    the manifest can be recognised among them by file name."""
    for k in range(n):
        EventImage.objects.create(
            event=event, event_day=day, is_primary=(primary and k == 0), sort_order=k,
            image=f"portals/p/events/{event.pk}/gallery/{uuid.uuid4()}/{uuid.uuid4()}.jpg",
        )


@override_settings(USE_R2_STORAGE=False)
class AttachAgainstProdShapesTests(TestCase):
    """
    The REAL committed manifest against events shaped exactly like production
    (read-only prod check, coordinator brief):

      winifred-ojularis-birthday  no cover; days by date:
                                  "Pre-Birthday Photoshoot" (0 images),
                                  "Event No. 1" (14, headline "Rooted in Gratitude..."),
                                  "Event No. 2" (19, headline "Fifty, Unforgettable...")
      josephine-inwangs-birthday  1 day "Event No. 1" (17 images) + event-level cover
      msme-engagement             no images, no days

    Every prod image has a UUID basename, so file-name dedupe finds nothing;
    the empty-target rule is what keeps --gallery from duplicating them.
    Images root is a temp dir of 2x2 stand-ins for every manifest path.
    """

    FLAGS = ("--set-public-slug", "--day-slugs", "--cover", "--gallery", "--publish")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "public"
        manifest = json.loads(DEFAULT_MANIFEST.read_text())
        paths = set()
        for ev in manifest:
            paths.add(ev["coverImage"])
            for row in ev.get("gallery") or []:
                paths.update(row.get("images") or [])
            for sub in ev.get("subEvents") or []:
                paths.add(sub["image"])
                for row in sub.get("gallery") or []:
                    paths.update(row.get("images") or [])
        for web_path in paths:
            local = self.root / web_path.lstrip("/")
            local.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (2, 2), "white").save(local, format="JPEG")

        client = User.objects.create_user(
            first_name="Winifred", last_name="Ojulari", email="prodshape@example.com", password="x",
        )
        mk = lambda title, **kw: Event.objects.create(  # noqa: E731
            celebrant=client, title=title, country="Nigeria", state="Lagos", **kw,
        )
        self.golden = mk(
            "Winifred Ojulari's Birthday", event_type="Birthday", event_date=datetime.date(2021, 3, 1),
            headline="A Golden 50th: An Intimate Two-Day Celebration of Family, Faith & Joy",
        )
        self.photoshoot = EventDay.objects.create(
            owner=self.golden, event_day_title="Pre-Birthday Photoshoot", date=datetime.date(2021, 3, 1),
        )
        self.no1 = EventDay.objects.create(
            owner=self.golden, event_day_title="Event No. 1", date=datetime.date(2021, 3, 2),
            headline="Rooted in Gratitude",  # truncated relative to the manifest title
        )
        self.no2 = EventDay.objects.create(
            owner=self.golden, event_day_title="Event No. 2", date=datetime.date(2021, 3, 3),
            headline="Fifty, Unforgettable - An Evening of Music, Dance & Celebration",
        )
        _uuid_images(self.golden, self.no1, 14)
        _uuid_images(self.golden, self.no2, 19)

        self.josephine = mk(
            "Josephine Inwang's Birthday", event_type="Birthday", event_date=datetime.date(2022, 5, 1),
            headline="An Intimate 85th: A Celebration of Grace, Family & Legacy",
        )
        self.j_day = EventDay.objects.create(
            owner=self.josephine, event_day_title="Event No. 1", date=datetime.date(2022, 5, 1),
        )
        _uuid_images(self.josephine, self.j_day, 17)
        _uuid_images(self.josephine, None, 1)  # the event-level cover

        self.msme = mk(
            "MSME Engagement", event_type="Corporate", event_date=datetime.date(2026, 2, 1),
            headline="Lagos State Government MSME Engagement Forum",
        )
        cache.clear()

    def _run(self, static, event, *flags):
        out = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command(
                "attach_static_portfolio", "--static-slug", static, "--event", event.slug,
                "--images-root", str(self.root), *flags, stdout=out,
            )
        return out.getvalue()

    def _counts(self, event):
        return {
            "event": event.images.filter(event_day__isnull=True).count(),
            **{d.event_day_title: d.images.count() for d in event.days.all()},
        }

    def test_golden_days_map_photoshoot_thanksgiving_celebration_in_order(self):
        self._run("golden-50th", self.golden, "--day-slugs", "--commit")
        self.assertEqual(
            [(d.pk, d.slug) for d in self.golden.days.all()],
            [(self.photoshoot.pk, "pre-birthday-photoshoot"),
             (self.no1.pk, "thanksgiving-gathering"),
             (self.no2.pk, "celebration-night")],
        )

    def test_headline_is_preferred_over_a_misleading_eyebrow(self):
        """Eyebrows swapped by mistake; the headlines still identify the days."""
        EventDay.objects.filter(pk=self.no1.pk).update(event_day_title="Event No. 2")
        EventDay.objects.filter(pk=self.no2.pk).update(event_day_title="Event No. 1")
        out = self._run("golden-50th", self.golden, "--day-slugs")
        self.assertIn("by title (headline first", out)
        self.assertIn(f"SET day {self.no1.pk} slug 'event-no-1' -> 'thanksgiving-gathering'", out)
        self.assertIn(f"SET day {self.no2.pk} slug 'event-no-2' -> 'celebration-night'", out)

    def test_golden_uploads_cover_and_photoshoot_day_only(self):
        dry = self._run("golden-50th", self.golden, *self.FLAGS)
        self.assertIn("SKIP day 'Event No. 1': already has 14 images", dry)
        self.assertIn("SKIP day 'Event No. 2': already has 19 images", dry)
        # public slug + 2 day slugs (day 1's already matches) + cover + 15
        # photoshoot images (one of them the cover again, as on the static
        # site) + publish.
        self.assertIn("Summary: 20 change(s), 16 upload(s).", dry)

        self._run("golden-50th", self.golden, *self.FLAGS, "--commit")
        self.assertEqual(
            self._counts(self.golden),
            {"event": 1, "Pre-Birthday Photoshoot": 15, "Event No. 1": 14, "Event No. 2": 19},
        )
        self.assertTrue(self.photoshoot.images.get(sort_order=0).is_primary)
        self.assertIn("(nothing to do)", self._run("golden-50th", self.golden, *self.FLAGS, "--commit"))

    def test_josephine_uploads_nothing(self):
        dry = self._run("intimate-85th", self.josephine, *self.FLAGS)
        self.assertIn("SKIP event level: already has 1 image", dry)
        self.assertIn("cover: event already has an event-level primary", dry)
        self.assertIn("0 upload(s)", dry)
        self._run("intimate-85th", self.josephine, *self.FLAGS, "--commit")
        self.assertEqual(self._counts(self.josephine), {"event": 1, "Event No. 1": 17})

    def test_msme_uploads_cover_and_gallery(self):
        dry = self._run("msme-forum", self.msme, *self.FLAGS)
        self.assertIn("14 upload(s)", dry)
        self._run("msme-forum", self.msme, *self.FLAGS, "--commit")
        self.assertEqual(self._counts(self.msme), {"event": 14})

    def test_force_gallery_would_duplicate_and_says_so(self):
        dry = self._run("golden-50th", self.golden, "--day-slugs", "--force-gallery")
        self.assertIn("WILL BE DUPLICATED", dry)
        self.assertNotIn("SKIP day", dry)
        self.assertIn("50 upload(s)", dry)  # every manifest day image: UUID names never match
