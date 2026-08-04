"""
Rewrite existing ClientDocument rows from the old category value "contract" to
its new value "svc_agreement" (the enum member CONTRACT -> SVC_AGREEMENT; the
client-facing label "Service Agreement" is unchanged, and reference codes keep
their "C" type letter). Reversible.
"""

from django.db import migrations


def contract_to_svc_agreement(apps, schema_editor):
    ClientDocument = apps.get_model("document_hub", "ClientDocument")
    ClientDocument.objects.filter(category="contract").update(category="svc_agreement")


def svc_agreement_to_contract(apps, schema_editor):
    ClientDocument = apps.get_model("document_hub", "ClientDocument")
    ClientDocument.objects.filter(category="svc_agreement").update(category="contract")


class Migration(migrations.Migration):

    dependencies = [
        ("document_hub", "0010_alter_clientdocument_category"),
    ]

    operations = [
        migrations.RunPython(contract_to_svc_agreement, svc_agreement_to_contract),
    ]
