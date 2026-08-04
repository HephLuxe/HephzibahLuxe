"""
Initialise the global "engagement" reference counter so newly auto-generated
segments (PSW001, PSW002, …) start ABOVE any segment already present in
existing, hand-typed reference codes — otherwise a new engagement's
HL-PSW001-C001 would collide with an old staff-typed HL-PSW001-C001.

Best-effort: scan existing ClientDocument/Invoice/Receipt codes for the
`HL-<PREFIX><digits>-…` shape and set the counter to the max number found.
Codes with a non-PREFIX middle segment can't collide with PSW### and are
ignored. Reversible to a no-op.
"""

import re

from django.conf import settings
from django.db import migrations


def init_engagement_counter(apps, schema_editor):
    ClientDocument = apps.get_model("document_hub", "ClientDocument")
    Invoice = apps.get_model("document_hub", "Invoice")
    Receipt = apps.get_model("document_hub", "Receipt")
    ReferenceCounter = apps.get_model("document_hub", "ReferenceCounter")

    prefix = getattr(settings, "REFERENCE_SEGMENT_PREFIX", "PSW")
    pattern = re.compile(rf"^HL-{re.escape(prefix)}0*(\d+)-")

    codes = []
    codes += list(ClientDocument.objects.exclude(reference_code="").values_list("reference_code", flat=True))
    codes += list(Invoice.objects.values_list("invoice_number", flat=True))
    codes += list(Receipt.objects.values_list("receipt_number", flat=True))

    max_number = 0
    for code in codes:
        match = pattern.match(code or "")
        if match:
            max_number = max(max_number, int(match.group(1)))

    if max_number:
        ReferenceCounter.objects.update_or_create(scope="engagement", defaults={"value": max_number})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("document_hub", "0006_referencecounter"),
    ]

    operations = [
        migrations.RunPython(init_engagement_counter, noop),
    ]
