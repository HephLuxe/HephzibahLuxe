"""
Normalize existing free-text venue_booking_status values onto the new
VenueBookingStatus enum (e.g. "Confirmed" -> "confirmed"). Anything that doesn't
map to a known status is cleared to "" (unset), matching the field's blank
default. Reversible to a no-op.
"""

from django.db import migrations

# Maps lowercased legacy free text -> enum value.
_MAP = {
    "confirmed": "confirmed",
    "pending": "pending",
    "not booked": "not_booked",
    "not_booked": "not_booked",
    "unbooked": "not_booked",
}


def normalize(apps, schema_editor):
    EventDay = apps.get_model("events", "EventDay")
    for day in EventDay.objects.exclude(venue_booking_status=""):
        mapped = _MAP.get((day.venue_booking_status or "").strip().lower(), "")
        if mapped != day.venue_booking_status:
            day.venue_booking_status = mapped
            day.save(update_fields=["venue_booking_status"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0006_event_last_updated_by_eventday_last_updated_by_and_more"),
    ]

    operations = [
        migrations.RunPython(normalize, noop),
    ]
