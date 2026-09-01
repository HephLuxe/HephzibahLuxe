"""
Re-derive every invoice-driven milestone from its invoices.

The repair hatch for any invoice change that bypassed the service layer: a
direct SQL/admin-DB edit, a bulk `queryset.update()`, or a status flip served by
a deploy that predates services.sync_invoice_milestone. In all three the invoice
row is right and the milestone is stale, which is invisible until someone reads
the Payment Overview and finds a paid invoice sitting against a pending
milestone.

Idempotent and safe to re-run: sync_milestone_from_invoices recomputes from the
whole invoice set rather than applying a delta, so running this twice is the
same as running it once. Milestones with NO invoices are skipped entirely --
those are ad-hoc or pre-link rows whose payment is recorded on the milestone
itself, and deriving them from an empty invoice set would erase it.

    python manage.py resync_payment_schedules                  # dry run
    python manage.py resync_payment_schedules --apply
    python manage.py resync_payment_schedules --engagement <uuid> --apply
"""

from django.core.management.base import BaseCommand

from apps.document_hub.models import PaymentMilestone
from apps.document_hub.services import sync_milestone_from_invoices


class Command(BaseCommand):
    help = "Recompute milestone amount_paid/status from their invoices."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Write the corrections. Without this the command only reports.",
        )
        parser.add_argument(
            "--engagement", default=None,
            help="Limit to one engagement id. Default: every milestone that has invoices.",
        )
        parser.add_argument(
            "--notify", action="store_true",
            help=(
                "Also email clients about milestones this settles. OFF by default: a "
                "repair writes a transition that already happened in the invoice "
                "record, and emailing would tell the client their payment cleared "
                "today because staff fixed a drift."
            ),
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        if apply_changes and options["notify"]:
            self.stdout.write(self.style.WARNING("--notify: clients WILL be emailed about settled milestones.\n"))

        milestones = (
            PaymentMilestone.objects.filter(invoices__isnull=False)
            .distinct()
            .select_related("schedule__engagement")
            .order_by("schedule_id", "order")
        )
        if options["engagement"]:
            milestones = milestones.filter(schedule__engagement_id=options["engagement"])

        drifted = 0
        for milestone in milestones:
            before = (milestone.amount_paid, milestone.status)

            if not apply_changes:
                # Predict without writing: same arithmetic the service uses.
                from django.db.models import Sum

                from apps.document_hub.models import InvoiceStatus
                from apps.document_hub.services import derive_milestone_status

                milestone.amount_paid = milestone.invoices.filter(
                    status=InvoiceStatus.PAID
                ).aggregate(total=Sum("amount"))["total"] or 0
                milestone.status = derive_milestone_status(milestone)
            else:
                sync_milestone_from_invoices(milestone, notify=options["notify"])

            after = (milestone.amount_paid, milestone.status)
            if before != after:
                drifted += 1
                self.stdout.write(
                    f"{milestone.schedule.engagement}  {milestone.label}: "
                    f"{before[0]} ({before[1]})  ->  {after[0]} ({after[1]})"
                )

        if not drifted:
            self.stdout.write(self.style.SUCCESS("Every milestone already agrees with its invoices."))
        elif apply_changes:
            self.stdout.write(self.style.SUCCESS(f"\nCorrected {drifted} milestone(s)."))
        else:
            self.stdout.write(
                self.style.WARNING(f"\n{drifted} milestone(s) out of sync. Dry run — re-run with --apply.")
            )
