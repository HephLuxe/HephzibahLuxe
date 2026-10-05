"""
apps/events/test_import_static_portfolio.py

The static-portfolio importer (management/commands/import_static_portfolio.py).
Driven with a small generated manifest and tiny generated JPEGs so the suite
does not upload the frontend's ~190MB of photographs; one test checks that the
committed manifest itself maps cleanly.
"""

import datetime
import json
import shutil
import tempfile
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
from apps.events.management.commands.import_static_portfolio import DEFAULT_MANIFEST, build_plan
from apps.events.models import Event, EventDay, EventImage
from apps.notifications.models import Notification
from apps.portal.models import EventEngagement

User = get_user_model()
factory = APIRequestFactory()

MANIFEST = [
    {
        "slug": "golden-test",
        "type": "multi-day",
        "category": "Birthdays",
        "location": "Lagos, Nigeria",
        "year": 2021,
        "title": "A Golden Test: Two Days",
        "coverImage": "/images/p/cover.jpg",
        "description": ["First paragraph.", "Second paragraph."],
        "subEvents": [
            {
                "subtitle": "Pre-Birthday Photoshoot",
                "title": "A Moment Before",
                "image": "/images/p/day1.jpg",
                "slug": "pre-birthday",
                "description": ["Day one story."],
                "gallery": [
                    {"type": "images", "images": ["/images/p/a.jpg", "/images/p/b.jpg"]},
                    {"type": "testimonial", "quote": "Lovely.", "attribution": "Someone"},
                    # Repeats the day's primary: must be uploaded once.
                    {"type": "images", "images": ["/images/p/day1.jpg"], "ratios": [1]},
                ],
            },
            {
                "subtitle": "Event No. 1",
                "title": "Rooted",
                "image": "/images/p/day2.jpg",
                "slug": "rooted",
            },
        ],
    },
    {
        "slug": "forum-test",
        "type": "single-day",
        "category": "Corporate",
        "location": "Lagos, Nigeria",
        "year": 2026,
        "title": "A Forum",
        "coverImage": "/images/p/forum-cover.jpg",
        "description": ["Forum story."],
        "gallery": [
            {"type": "images", "images": ["/images/p/f1.jpg", "/images/p/f2.jpg"]},
            {"type": "testimonial", "quote": "Great.", "attribution": "A Manager"},
        ],
    },
]


@override_settings(USE_R2_STORAGE=False)
class ImportStaticPortfolioTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.images_root = self.tmp / "public"
        (self.images_root / "images" / "p").mkdir(parents=True)
        for name in ("cover", "day1", "a", "b", "day2", "forum-cover", "f1", "f2"):
            Image.new("RGB", (2, 2), "white").save(self.images_root / "images" / "p" / f"{name}.jpg")
        self.manifest = self.tmp / "manifest.json"
        self.manifest.write_text(json.dumps(MANIFEST))
        cache.clear()

    def _run(self, *extra):
        out = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command(
                "import_static_portfolio",
                "--manifest", str(self.manifest),
                "--images-root", str(self.images_root),
                *extra, stdout=out,
            )
        return out.getvalue()

    # ── dry run ──────────────────────────────────────────────────────────────

    def test_dry_run_writes_nothing_and_uploads_nothing(self):
        with mock.patch.object(InMemoryStorage, "save", side_effect=AssertionError("upload")):
            output = self._run()

        self.assertIn("DRY RUN", output)
        self.assertIn("CREATE golden-test (public_slug)", output)
        self.assertIn("would create 2, skipped 0", output)
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(EventDay.objects.count(), 0)
        self.assertEqual(EventImage.objects.count(), 0)

    def test_dry_run_then_commit_in_one_transaction(self):
        """The dry run's READ ONLY marker must not outlive it (it runs in a
        savepoint here, inside the test's transaction)."""
        self._run()
        self._run("--commit")
        self.assertEqual(Event.objects.count(), 2)

    # ── commit ───────────────────────────────────────────────────────────────

    def test_commit_creates_the_expected_rows(self):
        output = self._run("--commit")
        self.assertIn("created 2, skipped 0", output)

        # The static slug is the PUBLIC slug; the internal one is save()'s own.
        golden = Event.objects.get(public_slug="golden-test")
        self.assertNotEqual(golden.slug, "golden-test")
        self.assertTrue(golden.slug)
        self.assertTrue(golden.is_published)
        self.assertEqual(golden.headline, "A Golden Test: Two Days")
        self.assertEqual(golden.event_type, "Birthday")
        self.assertEqual((golden.state, golden.country), ("Lagos", "Nigeria"))
        self.assertEqual(golden.event_date, datetime.date(2021, 1, 1))
        self.assertEqual(golden.description, "First paragraph.\n\nSecond paragraph.")
        self.assertIsNone(golden.celebrant)

        event_level = golden.images.filter(event_day__isnull=True)
        self.assertEqual(event_level.count(), 1)
        self.assertTrue(event_level.get().is_primary)
        # Upload path carries the event pk only — no slug of either kind.
        self.assertIn(f"/events/{golden.pk}/gallery/", event_level.get().image.name)
        self.assertNotIn(golden.slug, event_level.get().image.name)

        days = list(golden.days.all())
        self.assertEqual([d.event_day_title for d in days], ["Pre-Birthday Photoshoot", "Event No. 1"])
        self.assertEqual([d.headline for d in days], ["A Moment Before", "Rooted"])
        self.assertEqual([d.slug for d in days], ["pre-birthday", "rooted"])
        self.assertEqual([d.date for d in days], [datetime.date(2021, 1, 1), datetime.date(2021, 1, 2)])
        day1_images = list(days[0].images.all())
        # primary + a + b; the repeated day1.jpg is not uploaded twice.
        self.assertEqual(len(day1_images), 3)
        self.assertEqual([i.is_primary for i in day1_images], [True, False, False])
        self.assertEqual(day1_images[0].alt_text, "A Moment Before — photo 1")

        forum = Event.objects.get(public_slug="forum-test")
        self.assertEqual(forum.event_type, "Corporate")
        self.assertEqual(forum.days.count(), 0)
        self.assertEqual(forum.images.count(), 3)  # cover + 2 gallery

        self.assertIn("1 testimonial row(s)", output)
        # No client-facing side effects.
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(EventEngagement.objects.count(), 0)

    def test_imported_events_render_on_the_public_api(self):
        self._run("--commit")

        listing = public_views.portfolio_events(factory.get("/")).data
        self.assertEqual(sorted(e["slug"] for e in listing), ["forum-test", "golden-test"])

        forum = public_views.portfolio_event_detail(factory.get("/"), slug="forum-test").data
        self.assertEqual(forum["year"], 2026)
        self.assertIsNotNone(forum["cover_image"])
        self.assertEqual(len(forum["images"]), 2)  # gallery, cover excluded

        golden = public_views.portfolio_event_detail(factory.get("/"), slug="golden-test").data
        self.assertEqual(len(golden["event_days"]), 2)
        self.assertEqual([d["slug"] for d in golden["event_days"]], ["pre-birthday", "rooted"])
        self.assertEqual(len(golden["event_days"][0]["images"]), 3)
        self.assertEqual(golden["images"], [])

    def test_a_second_run_is_idempotent(self):
        self._run("--commit")
        counts = (Event.objects.count(), EventDay.objects.count(), EventImage.objects.count())

        output = self._run("--commit")

        self.assertIn("SKIP golden-test", output)
        self.assertIn("created 0, skipped 2", output)
        self.assertEqual(
            (Event.objects.count(), EventDay.objects.count(), EventImage.objects.count()), counts,
        )

    def test_an_existing_unpublished_event_with_the_public_slug_is_left_alone(self):
        existing = Event.objects.create(
            title="Someone's Party", country="NG", state="Lagos",
            event_date=datetime.date(2020, 1, 1), public_slug="golden-test",
        )

        output = self._run("--commit")

        self.assertIn("SKIP golden-test", output)
        existing.refresh_from_db()
        self.assertFalse(existing.is_published)
        self.assertEqual(existing.title, "Someone's Party")
        self.assertEqual(Event.objects.filter(public_slug="forum-test").count(), 1)

    def test_an_internal_slug_match_does_not_block_the_import(self):
        """Only the public slug is the identity here; an unrelated event whose
        INTERNAL slug happens to be the static one is not touched and does not
        stop the import."""
        other = Event.objects.create(
            title="Golden Test", country="NG", state="Lagos",
            event_date=datetime.date(2020, 1, 1),
        )
        Event.objects.filter(pk=other.pk).update(slug="golden-test")

        self._run("--commit")

        self.assertEqual(Event.objects.get(public_slug="golden-test").headline, "A Golden Test: Two Days")
        other.refresh_from_db()
        self.assertIsNone(other.public_slug)
        self.assertFalse(other.is_published)

    def test_portal_sets_the_celebrant(self):
        client = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="owner@example.com", password="x",
        )
        self._run("--commit", "--portal", "owner@example.com")
        self.assertEqual(Event.objects.get(public_slug="forum-test").celebrant, client)

        image = Event.objects.get(public_slug="forum-test").images.first()
        self.assertTrue(image.image.name.startswith(f"portals/{client.portal.pk}/"))

    def test_an_unknown_portal_is_an_error_before_anything_is_written(self):
        with self.assertRaises(CommandError):
            self._run("--commit", "--portal", "nobody@example.com")
        self.assertEqual(Event.objects.count(), 0)

    def test_a_missing_image_is_an_error_before_anything_is_written(self):
        (self.images_root / "images" / "p" / "f2.jpg").unlink()
        with self.assertRaises(CommandError):
            self._run("--commit")
        self.assertEqual(Event.objects.count(), 0)


class StaticManifestTests(TestCase):
    """The committed manifest generated from frontend/data/portfolio.ts."""

    def test_the_committed_manifest_maps_cleanly(self):
        plan = build_plan(json.loads(DEFAULT_MANIFEST.read_text()))
        by_slug = {p["slug"]: p for p in plan}

        self.assertEqual(sorted(by_slug), ["golden-50th", "intimate-85th", "msme-forum"])
        self.assertEqual(len(by_slug["golden-50th"]["days"]), 3)
        self.assertEqual(by_slug["intimate-85th"]["days"], [])
        self.assertEqual(by_slug["msme-forum"]["fields"]["event_type"], "Corporate")
        for p in plan:
            self.assertEqual((p["fields"]["state"], p["fields"]["country"]), ("Lagos", "Nigeria"))
