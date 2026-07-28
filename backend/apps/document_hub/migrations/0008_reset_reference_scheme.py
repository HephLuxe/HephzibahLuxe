"""
Reset the reference-numbering state for the new segment scheme.

The segment used to be a literal prefix + a single global engagement counter
(PSW001, PSW002, …). It now encodes the couple/honoree initials, the event-type
letter, and a global per-event-type counter (e.g. PSW006). Since there is no
real client data yet, wipe the old numbering state so new segments derive fresh
under the new scheme with no old/new collision:

  * delete all ReferenceCounter rows (every scope restarts at 1), and
  * null out EventEngagement.reference_segment so each engagement re-derives its
    segment lazily on the next document/invoice/receipt creation.

Reversible to a no-op — the state is regenerated on demand.
"""

from django.db import migrations


def reset_reference_state(apps, schema_editor):
    ReferenceCounter = apps.get_model("document_hub", "ReferenceCounter")
    EventEngagement = apps.get_model("portal", "EventEngagement")

    ReferenceCounter.objects.all().delete()
    EventEngagement.objects.exclude(reference_segment__isnull=True).update(reference_segment=None)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("document_hub", "0007_init_engagement_counter"),
        ("portal", "0007_eventengagement_reference_segment"),
    ]

    operations = [
        migrations.RunPython(reset_reference_state, noop),
    ]
