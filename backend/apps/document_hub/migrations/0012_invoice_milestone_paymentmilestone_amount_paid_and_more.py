"""
Link invoices to the milestones they bill for, and give a milestone a paid
AMOUNT rather than only a paid flag. See apps/document_hub/services.py
("Invoices drive the payment schedule") for why.

The RunPython below is not optional bookkeeping: `PaymentSchedule.paid_to_date`
now sums `amount_paid`, and every milestone already marked paid has
amount_paid=0 from the AddField default. Without the backfill this migration
would silently reset every client's Payment Overview to "nothing paid" the
moment it ran.
"""


import django.db.models.deletion
from django.db import migrations, models


def backfill_amount_paid(apps, schema_editor):
    """A milestone already flagged paid has had its full amount paid."""
    PaymentMilestone = apps.get_model("document_hub", "PaymentMilestone")
    PaymentMilestone.objects.filter(status="paid").update(
        amount_paid=models.F("amount")
    )


class Migration(migrations.Migration):

    dependencies = [
        ('document_hub', '0011_rename_contract_category_to_svc_agreement'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='milestone',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invoices', to='document_hub.paymentmilestone'),
        ),
        migrations.AddField(
            model_name='paymentmilestone',
            name='amount_paid',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=15),
        ),
        migrations.AlterField(
            model_name='invoice',
            name='due_on',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='paymentmilestone',
            name='status',
            field=models.CharField(choices=[('paid', 'Paid'), ('part_paid', 'Partly paid'), ('pending', 'Pending')], default='pending', max_length=10),
        ),
        migrations.RunPython(
            backfill_amount_paid, reverse_code=migrations.RunPython.noop
        ),
    ]
