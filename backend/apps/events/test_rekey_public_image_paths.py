"""
apps/events/test_rekey_public_image_paths.py

The rekey_public_image_paths command: moves gallery images stored under the old
``{event_id}-{event.slug}`` folder to the current upload path. Runs against the
storage the field really uses under the test runner (InMemoryStorage).
"""

import datetime
import io
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.events.management.commands import rekey_public_image_paths as rekey
from apps.events.models import EventDay, EventImage
from apps.events.public_views import _LIST_CACHE_KEY, _detail_cache_key
from apps.events.tests import _make_event, _png
from apps.notifications.models import Notification

User = get_user_model()


def _old_style(img: EventImage, content: bytes) -> str:
    """Re-home ``img`` at the pre-change path (folder carries the event slug)."""
    storage = img.image.storage
    event = img.event
    portal_id = event.celebrant.portal.id
    folder = f"portals/{portal_id}/events/{event.pk}-{event.slug}"
    if img.event_day_id:
        folder += f"/days/{img.event_day_id}"
    old = f"{folder}/gallery/{img.pk}/{uuid.uuid4()}.png"
    saved = storage.save(old, ContentFile(content))
    assert saved == old
    storage.delete(img.image.name)
    EventImage.objects.filter(pk=img.pk).update(image=old)
    return old


class _RekeyFixture(TestCase):
    """Two images (event level + day level) re-homed on old-style keys."""

    def setUp(self):
        self.client_user = User.objects.create_user(
            first_name="Winifred", last_name="Ojulari", email="rekey@example.com", password="x",
        )
        self.event = _make_event(self.client_user, title="Winifred Ojulari's Birthday")
        self.event.headline = "A Golden 50th"
        self.event.is_published = True
        self.event.save()
        self.day = EventDay.objects.create(
            owner=self.event, event_day_title="Event No. 1", date=datetime.date(2027, 6, 1),
        )
        self.storage = EventImage._meta.get_field("image").storage

        a = EventImage.objects.create(event=self.event, image=_png("a.png"), is_primary=True)
        b = EventImage.objects.create(event=self.event, event_day=self.day, image=_png("b.png"))
        self.images = [a, b]
        self.contents = [b"event-level-bytes", b"day-level-bytes!!"]
        self.old = [_old_style(img, c) for img, c in zip(self.images, self.contents)]
        self.new = [
            EventImage._meta.get_field("image").generate_filename(img, name.rsplit("/", 1)[1])
            for img, name in zip(self.images, self.old)
        ]

    def _run(self, *args):
        out, err = io.StringIO(), io.StringIO()
        call_command("rekey_public_image_paths", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def _names(self):
        return [EventImage.objects.get(pk=img.pk).image.name for img in self.images]

    def _read(self, name):
        with self.storage.open(name, "rb") as fh:
            return fh.read()


class RekeyPublicImagePathsTests(_RekeyFixture):
    def test_the_old_path_carries_the_slug_and_the_new_one_does_not(self):
        self.assertIn(self.event.slug, self.old[0])
        for name in self.new:
            self.assertNotIn(self.event.slug, name)
            self.assertNotIn("winifred", name)
        self.assertIn(f"/days/{self.day.pk}/gallery/", self.new[1])

    def test_dry_run_reports_the_mapping_and_writes_nothing(self):
        out, _ = self._run()
        self.assertIn("2 image(s) to re-key.", out)
        for old, new in zip(self.old, self.new):
            self.assertIn(f"{old} -> {new}  [will copy]", out)
        self.assertEqual(self._names(), self.old)
        for old, new in zip(self.old, self.new):
            self.assertTrue(self.storage.exists(old))
            self.assertFalse(self.storage.exists(new))

    def test_commit_copies_files_updates_rows_and_retains_old_objects(self):
        cache.set(_LIST_CACHE_KEY, ["stale"])
        cache.set(_detail_cache_key(self.event.public_slug), {"stale": True})
        notifications = Notification.objects.count()

        out, err = self._run("--commit")

        self.assertIn("Re-keyed 2 image(s).", out)
        self.assertEqual(err, "")
        self.assertEqual(self._names(), self.new)
        for old, new, content in zip(self.old, self.new, self.contents):
            self.assertTrue(self.storage.exists(old))
            self.assertEqual(self._read(new), content)
        self.assertIsNone(cache.get(_LIST_CACHE_KEY))
        self.assertIsNone(cache.get(_detail_cache_key(self.event.public_slug)))
        self.assertEqual(Notification.objects.count(), notifications)

    def test_rerun_is_a_no_op(self):
        self._run("--commit")
        out, _ = self._run("--commit")
        self.assertIn("0 image(s) to re-key.", out)
        out, _ = self._run()
        self.assertIn("0 image(s) to re-key.", out)
        self.assertEqual(self._names(), self.new)

    def test_limit(self):
        out, _ = self._run("--commit", "--limit", "1")
        self.assertIn("1 image(s) to re-key.", out)
        self.assertEqual(self._names(), [self.new[0], self.old[1]])
        out, _ = self._run()
        self.assertIn("1 image(s) to re-key.", out)

    def test_a_copy_failure_leaves_rows_and_old_objects_untouched(self):
        real = rekey._read_and_save
        calls = []

        def flaky(storage, old, new):
            calls.append(old)
            if len(calls) == 2:
                raise OSError("network went away")
            return real(storage, old, new)

        with mock.patch.object(rekey, "_read_and_save", side_effect=flaky):
            with self.assertRaisesMessage(CommandError, "No database row was changed"):
                self._run("--commit")

        self.assertEqual(self._names(), self.old)
        for old, content in zip(self.old, self.contents):
            self.assertEqual(self._read(old), content)

        # The copy that did land is reused on the re-run, not duplicated.
        out, _ = self._run("--commit")
        self.assertIn(f"present: {self.old[0]} -> {self.new[0]}", out)
        self.assertIn(f"copied: {self.old[1]} -> {self.new[1]}", out)
        self.assertEqual(self._names(), self.new)

    def test_conflicting_destination_is_reported_on_stderr_and_fails(self):
        """Same size, different bytes: a conflict. Never skipped silently — the
        dry run and the commit both name it on stderr and exit non-zero; the
        commit still moves every other row."""
        conflict = b"X" * len(self.contents[1])
        self.storage.save(self.new[1], ContentFile(conflict))

        out, err = io.StringIO(), io.StringIO()
        with self.assertRaisesMessage(CommandError, "1 conflicting destination(s)"):
            call_command("rekey_public_image_paths", stdout=out, stderr=err)
        self.assertIn("CONFLICT: target exists with different content", out.getvalue())
        self.assertIn(f"CONFLICT image {self.images[1].pk}: {self.new[1]}", err.getvalue())
        self.assertEqual(self._names(), self.old)

        out, err = io.StringIO(), io.StringIO()
        with self.assertRaisesMessage(CommandError, "1 conflicting destination(s)"):
            call_command("rekey_public_image_paths", "--commit", stdout=out, stderr=err)
        self.assertIn(f"CONFLICT image {self.images[1].pk}: {self.new[1]}", err.getvalue())
        self.assertIn("Re-keyed 1 image(s).", out.getvalue())
        self.assertEqual(self._names(), [self.new[0], self.old[1]])
        self.assertEqual(self._read(self.new[1]), conflict)
        for old in self.old:
            self.assertTrue(self.storage.exists(old))

    def test_corrupt_same_size_copy_never_updates_rows(self):
        def corrupt(storage, old, new):
            storage.save(new, ContentFile(b"X" * storage.size(old)))
        with mock.patch.object(rekey, "_read_and_save", side_effect=corrupt):
            with self.assertRaisesMessage(CommandError, "wrong content"):
                self._run("--commit")
        self.assertEqual(self._names(), self.old)

    def test_a_db_failure_rolls_back_every_row_and_keeps_old_objects(self):
        # Another writer changes the second row between plan and update.
        real_plan = rekey.plan_moves

        def plan_then_race(limit=None):
            result = real_plan(limit)
            EventImage.objects.filter(pk=self.images[1].pk).update(image="elsewhere.png")
            return result

        with mock.patch.object(rekey, "plan_moves", side_effect=plan_then_race):
            with self.assertRaisesMessage(CommandError, "rolled back"):
                self._run("--commit")
        self.assertEqual(EventImage.objects.get(pk=self.images[0].pk).image.name, self.old[0])
        for old in self.old:
            self.assertTrue(self.storage.exists(old))

    def test_commit_never_deletes_objects(self):
        with mock.patch.object(type(self.storage), "delete") as delete:
            self._run("--commit")
        delete.assert_not_called()

    def test_an_image_without_a_portal_is_skipped(self):
        EventImage.objects.filter(pk=self.images[0].pk).update(image="x/a.png")
        self.event.celebrant = None
        self.event.save()
        out, _ = self._run()
        self.assertIn("SKIP", out)
        self.assertIn("0 image(s) to re-key.", out)


class PurgeOldTests(_RekeyFixture):
    """--purge-old: the explicit, separate step that deletes the legacy objects
    the re-key keeps. Inherits the fixture (two images on old-style keys)."""

    def _purge(self, *args):
        return self._run("--purge-old", *args)

    def test_legacy_key_is_rederived_from_the_current_key_and_the_internal_slug(self):
        self._run("--commit")
        for img, old, new in zip(self.images, self.old, self.new):
            self.assertEqual(rekey.legacy_key(new, img.event_id, self.event.slug), old)
        # Not in the current format for this event -> no guess.
        self.assertIsNone(rekey.legacy_key(self.old[0], self.event.pk, self.event.slug))
        self.assertIsNone(rekey.legacy_key("home-strip/a.jpg", self.event.pk, self.event.slug))
        self.assertIsNone(rekey.legacy_key(self.new[0], self.event.pk + 1, self.event.slug))

    def test_dry_run_deletes_nothing_and_reports_counts(self):
        self._run("--commit")
        with mock.patch.object(type(self.storage), "delete") as delete:
            out, _ = self._purge()
        delete.assert_not_called()
        self.assertIn("2 legacy object(s) to delete", out)
        for old in self.old:
            self.assertIn(f"would delete {old}", out)
            self.assertTrue(self.storage.exists(old))
        self.assertIn("to_delete=2 legacy_not_found=0 not_rekeyed_yet=0", out)
        self.assertIn("Dry run: nothing deleted.", out)

    def test_commit_deletes_only_legacy_keys_whose_new_key_exists(self):
        # Image 0 re-keyed; image 1 not yet (still on its legacy key, which is
        # its CURRENT key and must survive).
        self._run("--commit", "--limit", "1")
        # A re-keyed row whose new object has vanished keeps its legacy object.
        other = EventImage.objects.create(event=self.event, image=_png("c.png"))
        other_old = _old_style(other, b"third")
        other_new = EventImage._meta.get_field("image").generate_filename(other, other_old.rsplit("/", 1)[1])
        EventImage.objects.filter(pk=other.pk).update(image=other_new)

        out, err = self._purge("--commit")

        self.assertIn("Deleted 1 legacy object(s).", out)
        self.assertFalse(self.storage.exists(self.old[0]))
        self.assertEqual(self._read(self.new[0]), self.contents[0])
        self.assertEqual(self._read(self.old[1]), self.contents[1])
        self.assertTrue(self.storage.exists(other_old))
        self.assertIn(f"KEEP current key missing from storage: {other.pk}", err)
        self.assertIn("to_delete=1 legacy_not_found=0 not_rekeyed_yet=1 current_missing=1", out)
        self.assertEqual(self._names(), [self.new[0], self.old[1]])

    def test_purge_is_idempotent(self):
        self._run("--commit")
        self._purge("--commit")
        for old, new, content in zip(self.old, self.new, self.contents):
            self.assertFalse(self.storage.exists(old))
            self.assertEqual(self._read(new), content)
        with mock.patch.object(type(self.storage), "delete") as delete:
            out, _ = self._purge("--commit")
        delete.assert_not_called()
        self.assertIn("0 legacy object(s) to delete", out)
        self.assertIn("to_delete=0 legacy_not_found=2", out)

    def test_purge_before_any_rekey_deletes_nothing(self):
        out, _ = self._purge("--commit")
        self.assertIn("to_delete=0 legacy_not_found=0 not_rekeyed_yet=2", out)
        for old in self.old:
            self.assertTrue(self.storage.exists(old))

    def test_purge_limit(self):
        self._run("--commit")
        out, _ = self._purge("--commit", "--limit", "1")
        self.assertIn("Deleted 1 legacy object(s).", out)
        self.assertEqual(sum(self.storage.exists(o) for o in self.old), 1)


class ServerSideCopyTests(TestCase):
    def test_s3_storage_uses_copy_object_with_normalized_keys(self):
        storage = mock.Mock(spec=["bucket_name", "connection", "_normalize_name"])
        storage.bucket_name = "public"
        storage._normalize_name.side_effect = lambda name: f"media/{name}"

        copy = rekey._server_side_copy(storage)
        copy("portals/p/events/1-slug/gallery/i/u.jpg", "portals/p/events/1/gallery/i/u.jpg")

        storage.connection.meta.client.copy_object.assert_called_once_with(
            Bucket="public",
            Key="media/portals/p/events/1/gallery/i/u.jpg",
            CopySource={"Bucket": "public", "Key": "media/portals/p/events/1-slug/gallery/i/u.jpg"},
        )

    def test_non_s3_storage_has_no_server_side_copy(self):
        self.assertIsNone(rekey._server_side_copy(EventImage._meta.get_field("image").storage))
