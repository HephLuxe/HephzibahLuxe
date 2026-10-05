"""
Backfill Event.public_slug and EventDay.slug for rows that predate them, using
the same rules save() now applies to new rows:

* Event.public_slug   <- slugify(headline), only where headline is non-empty.
                         Never from title/celebrant (title carries client names).
* EventDay.slug       <- slugify(event_day_title or headline), unique per event.

Collisions get -2, -3, ... suffixes. Deterministic: rows are visited in a fixed
order (events by pk; days by event, date, start_time, created_at, pk), so the
same data always yields the same slugs. Idempotent: only NULL/blank slugs are
filled, and slugs already present are treated as taken.

The generation logic is copied here rather than imported from models.py on
purpose: a migration has to keep producing the same result after the model code
moves on.

Reverse is a no-op: rolling back past 0015 drops both columns anyway, and
un-setting slugs here would also erase ones staff typed by hand.
"""

import re

from django.db import migrations
from django.utils.text import slugify

MAX_LENGTH = 100


def _pick(base: str, taken: set[str]) -> str | None:
    # slugify() keeps "_", the slug validator rejects it: "_" -> "-", and runs
    # of separators collapse to one.
    base = re.sub(r"[-_]+", "-", base)
    base = base[: MAX_LENGTH - 4].strip("-")
    if not base:
        return None
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def backfill(apps, schema_editor):
    Event = apps.get_model("events", "Event")
    EventDay = apps.get_model("events", "EventDay")

    taken = set(
        Event.objects.exclude(public_slug__isnull=True)
        .exclude(public_slug="")
        .values_list("public_slug", flat=True)
    )
    for event in Event.objects.order_by("pk"):
        if event.public_slug or not (event.headline or "").strip():
            continue
        slug = _pick(slugify(event.headline), taken)
        if slug:
            taken.add(slug)
            Event.objects.filter(pk=event.pk).update(public_slug=slug)

    days = EventDay.objects.order_by("owner_id", "date", "start_time", "created_at", "pk")
    taken_by_event: dict = {}
    for day in days:
        if day.slug:
            taken_by_event.setdefault(day.owner_id, set()).add(day.slug)
    for day in days:
        if day.slug:
            continue
        source = (day.event_day_title or "").strip() or (day.headline or "").strip()
        if not source:
            continue
        taken = taken_by_event.setdefault(day.owner_id, set())
        slug = _pick(slugify(source), taken)
        if slug:
            taken.add(slug)
            EventDay.objects.filter(pk=day.pk).update(slug=slug)


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0015_public_slugs"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
