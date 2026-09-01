"""
Carries the two retired single-image columns into the gallery, then drops them.

`Event.featured_image` and `EventDay.event_images` each held one image and are
replaced by an EventImage flagged `is_primary` — the cover. The current database
has no rows with either field set, so in practice this moves nothing. It is
written anyway, because "the field was empty everywhere" is a claim about one
database at one moment: a staging copy, a restored backup, or a colleague's local
data can all disagree, and the alternative to a no-op RunPython is silently
deleting someone's cover image with no way back.

The blob itself is not moved. Both retired fields stored under
``covers/cover.ext`` / ``days/<id>/images/image.ext``, and an EventImage's
``upload_to`` would place it under ``gallery/<image_id>/``, but the stored path is
just a string — repointing the row at the existing object keeps the image
serving from where it already lives and avoids a storage copy inside a migration,
which is neither transactional nor retryable. New uploads land in the gallery
layout; these carried-over rows keep their old path for ever, which is harmless.
"""

import uuid

from django.db import migrations


def carry_covers_into_gallery(apps, schema_editor):
    Event = apps.get_model("events", "Event")
    EventDay = apps.get_model("events", "EventDay")
    EventImage = apps.get_model("events", "EventImage")

    for event in Event.objects.exclude(featured_image="").exclude(featured_image=None):
        EventImage.objects.create(
            id=uuid.uuid4(), event=event, event_day=None,
            image=event.featured_image.name, is_primary=True, sort_order=0,
        )

    for day in EventDay.objects.exclude(event_images="").exclude(event_images=None):
        EventImage.objects.create(
            id=uuid.uuid4(), event_id=day.owner_id, event_day=day,
            image=day.event_images.name, is_primary=True, sort_order=0,
        )


def carry_covers_back(apps, schema_editor):
    """
    Reverse leg, so this migration is not a one-way door. Puts each primary image
    back on the field it came from; any additional gallery image has nowhere to go
    in the old schema and is dropped, which is inherent to reversing a
    one-to-many back into a one-to-one rather than a fault in this code.
    """
    Event = apps.get_model("events", "Event")
    EventDay = apps.get_model("events", "EventDay")
    EventImage = apps.get_model("events", "EventImage")

    for image in EventImage.objects.filter(is_primary=True, event_day__isnull=True):
        Event.objects.filter(pk=image.event_id).update(featured_image=image.image.name)

    for image in EventImage.objects.filter(is_primary=True, event_day__isnull=False):
        EventDay.objects.filter(pk=image.event_day_id).update(event_images=image.image.name)


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0011_eventimage'),
    ]

    operations = [
        # Order matters: read the columns, then drop them.
        migrations.RunPython(carry_covers_into_gallery, carry_covers_back),
        migrations.RemoveField(
            model_name='event',
            name='featured_image',
        ),
        migrations.RemoveField(
            model_name='eventday',
            name='event_images',
        ),
    ]
