"""
apps/events/test_slug_backfill_migration.py

The 0016 data migration that backfills Event.public_slug and EventDay.slug for
rows that predate them. Driven by calling its RunPython function against the
live app registry on rows whose slugs have been nulled with update() (which
skips the save() that would otherwise generate them).
"""

import datetime
import importlib

from django.apps import apps
from django.test import TestCase

from apps.events.models import Event, EventDay

backfill = importlib.import_module("apps.events.migrations.0016_backfill_public_slugs").backfill


class SlugBackfillMigrationTests(TestCase):
    def setUp(self):
        mk = lambda title, headline: Event.objects.create(  # noqa: E731
            title=title, headline=headline, country="NG", state="Lagos",
            event_date=datetime.date(2021, 1, 1),
        )
        self.golden = mk("Winifred Ojulari's Birthday", "A Golden 50th")
        self.twin = mk("Someone Else's Birthday", "A Golden 50th")
        self.bare = mk("No Headline", "")
        self.d1 = EventDay.objects.create(owner=self.golden, event_day_title="Event No. 1", date=datetime.date(2021, 1, 2))
        self.d0 = EventDay.objects.create(owner=self.golden, event_day_title="Pre-Birthday Photoshoot", date=datetime.date(2021, 1, 1))
        self.d2 = EventDay.objects.create(owner=self.golden, headline="Fifty, Unforgettable", date=datetime.date(2021, 1, 3))
        self.d3 = EventDay.objects.create(owner=self.golden, event_day_title="Event No. 1", date=datetime.date(2021, 1, 4))
        self.blank_day = EventDay.objects.create(owner=self.golden, date=datetime.date(2021, 1, 5))
        Event.objects.update(public_slug=None)
        EventDay.objects.update(slug=None)

    def _state(self):
        return (
            dict(Event.objects.values_list("pk", "public_slug")),
            dict(EventDay.objects.values_list("pk", "slug")),
        )

    def test_backfills_from_headline_and_day_title(self):
        backfill(apps, None)
        events, days = self._state()
        self.assertEqual(events[self.golden.pk], "a-golden-50th")
        self.assertEqual(events[self.twin.pk], "a-golden-50th-2")  # later pk gets the suffix
        self.assertIsNone(events[self.bare.pk])  # never from the title
        self.assertEqual(days[self.d0.pk], "pre-birthday-photoshoot")
        self.assertEqual(days[self.d1.pk], "event-no-1")  # earlier date wins the bare slug
        self.assertEqual(days[self.d3.pk], "event-no-1-2")
        self.assertEqual(days[self.d2.pk], "fifty-unforgettable")  # headline fallback
        self.assertIsNone(days[self.blank_day.pk])

    def test_idempotent_and_respects_existing_slugs(self):
        Event.objects.filter(pk=self.twin.pk).update(public_slug="a-golden-50th")
        backfill(apps, None)
        first = self._state()
        backfill(apps, None)
        self.assertEqual(self._state(), first)
        events, _ = first
        self.assertEqual(events[self.twin.pk], "a-golden-50th")  # kept
        self.assertEqual(events[self.golden.pk], "a-golden-50th-2")  # yields to the existing one

    def test_underscores_become_hyphens(self):
        Event.objects.filter(pk=self.bare.pk).update(headline="Ade_Bola _ Forever")
        EventDay.objects.filter(pk=self.blank_day.pk).update(event_day_title="Day_One")
        backfill(apps, None)
        events, days = self._state()
        self.assertEqual(events[self.bare.pk], "ade-bola-forever")
        self.assertEqual(days[self.blank_day.pk], "day-one")
        event = Event.objects.get(pk=self.bare.pk)
        # The backfilled slug passes the validator. The fixture has no
        # celebrant or venue, which full_clean would otherwise also report.
        event.full_clean(exclude=["celebrant", "event_venue"])
        event.save()
        EventDay.objects.get(pk=self.blank_day.pk).full_clean()

    def test_deterministic(self):
        backfill(apps, None)
        first = self._state()
        Event.objects.update(public_slug=None)
        EventDay.objects.update(slug=None)
        backfill(apps, None)
        self.assertEqual(self._state(), first)
