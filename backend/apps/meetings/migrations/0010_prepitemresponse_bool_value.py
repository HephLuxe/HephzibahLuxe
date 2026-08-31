"""Move checkbox answers off ``text_value`` and onto a real boolean column.

Before this, a checkbox stored the *string* "true"/"false" in ``text_value`` —
the write path in ``services.submit_field_response`` was a byte-for-byte copy of
the qa/text branch, so it accepted any prose at all, while the read path
(``field_is_answered``) compared ``text_value == "true"`` exactly. A client could
submit a paragraph to a required checkbox, receive a 200, and leave the field
permanently unanswerable.

The data migration reads the old string with the same strictness the old read
path used — only the exact lowercase ``"true"`` was ever treated as ticked, so
anything else becomes ``False``. That deliberately preserves each row's
*observable* completion state rather than trying to reinterpret prose.

``text_value`` is cleared on those rows in the same pass. It is dead for
checkboxes now, and leaving a stray paragraph there is the exact confusion this
change exists to remove.

**Irreversible by design.** The reverse leaves ``bool_value`` NULL and does not
restore the cleared prose — that text was invalid input in a checkbox field, and
recovering it is not worth carrying the ambiguity forward.
"""

from django.db import migrations, models


def checkbox_strings_to_booleans(apps, schema_editor):
    PrepItemResponse = apps.get_model("meetings", "PrepItemResponse")
    responses = PrepItemResponse.objects.filter(field__field_type="checkbox")
    for response in responses.iterator():
        # Matches the old read path exactly: only "true" counted as ticked.
        response.bool_value = response.text_value == "true"
        response.text_value = ""
        response.save(update_fields=["bool_value", "text_value"])


def noop_reverse(apps, schema_editor):
    """Reversing drops back to NULL. The prose is not recoverable — see module docstring."""


class Migration(migrations.Migration):

    dependencies = [
        ('meetings', '0009_meeting_created_by_meeting_last_updated_by_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='prepitemresponse',
            name='bool_value',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.RunPython(checkbox_strings_to_booleans, noop_reverse),
    ]
