"""
One-off repair for engagements whose invoices were raised BEFORE
Invoice.milestone existed — they mirror the schedule's milestones by hand, so
paying one moves nothing.

Deliberately a command and not a data migration. The pairing is a GUESS: it
matches the Nth invoice to the Nth milestone by amount, and a migration doing
that would run unattended on every deploy, silently, with no chance to look at
what it decided first. Here it prints the pairing and changes nothing unless
you pass --apply.

    python manage.py link_invoices_to_milestones                # dry run
    python manage.py link_invoices_to_milestones --apply
    python manage.py link_invoices_to_milestones --engagement <uuid> --apply
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.document_hub.models import Invoice, PaymentSchedule
from apps.document_hub.services import sync_milestone_from_invoices


class Command(BaseCommand):
    help = "Link pre-existing invoices to the payment milestones they bill for."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Write the links. Without this the command only reports.",
        )
        parser.add_argument(
            "--engagement", default=None,
            help="Limit to one engagement id. Default: every engagement with a schedule.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        schedules = PaymentSchedule.objects.select_related("engagement")
        if options["engagement"]:
            schedules = schedules.filter(engagement_id=options["engagement"])

        linked = skipped = 0

        for schedule in schedules:
            # Only ever touch invoices that have no milestone yet, so re-running
            # this can never re-point a link somebody set deliberately.
            invoices = list(
                Invoice.objects.filter(
                    engagement=schedule.engagement, milestone__isnull=True
                ).order_by("invoice_number")
            )
            if not invoices:
                continue

            milestones = list(schedule.milestones.filter(invoices__isnull=True).order_by("order"))
            self.stdout.write(f"\n{schedule.engagement} — {len(invoices)} unlinked invoice(s)")

            pairs = []
            for invoice in invoices:
                # Amount is the only evidence available. Order breaks ties, which
                # is why a 30/40/30 split (two milestones at the same amount)
                # resolves earliest-first and needs an eyeball before --apply.
                match = next((m for m in milestones if m.amount == invoice.amount), None)
                if match is None:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  {invoice.invoice_number}  {invoice.amount}  -> no unlinked "
                            "milestone with a matching amount; left alone"
                        )
                    )
                    skipped += 1
                    continue
                milestones.remove(match)
                pairs.append((invoice, match))
                self.stdout.write(
                    f"  {invoice.invoice_number}  {invoice.amount}  ->  "
                    f"{match.label} ({match.status})"
                )

            if not apply_changes:
                continue

            with transaction.atomic():
                for invoice, milestone in pairs:
                    invoice.milestone = milestone
                    invoice.save(update_fields=["milestone", "updated_at"])
                    linked += 1
                # After linking, re-derive each milestone from its invoices —
                # this is what finally moves paid_to_date for an invoice that
                # was already marked paid.
                for _, milestone in pairs:
                    milestone.refresh_from_db()
                    sync_milestone_from_invoices(milestone)

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(f"\nLinked {linked} invoice(s); skipped {skipped}."))
        else:
            self.stdout.write(
                self.style.WARNING("\nDry run — nothing written. Re-run with --apply.")
            )
