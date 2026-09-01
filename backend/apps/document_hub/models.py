"""
apps/document_hub/models.py

The "HL Client Document Hub" page: Service Agreement, Quotation, Welcome &
Service Information PDFs, the Payment Overview (milestone tracker), Invoices,
and Receipts.

Deliberately separate from apps.documents, which is a generic-FK store for
*internal* media assets (event covers, contact photos, prep uploads) produced
as a side effect of other actions. Everything here is a client-facing,
staff-authored record with its own lifecycle.

Reference codes (e.g. "HL-PSW006-C001", "HL-PSW006-Q001", "HL-PSW006-INV001",
"HL-PSW006-R001") are **auto-generated and read-only** — see
services.next_reference_code and the pre_save signals. Format:
HL-<engagement segment>-<TYPE><NNN>. The <segment> (e.g. PSW006) is assigned to
each EventEngagement on first need and encodes <initials><event-type letter>
<global per-event-type count> (Priscilla & Samuel's 6th wedding → PSW006 — see
services.assign_engagement_segment). The trailing <NNN> restarts at 001 per
engagement per type (C=Service Agreement, Q=Quotation, INV=Invoice, R=Receipt). The regex
validator still guards the format. (This reverses the earlier staff-typed scheme.)
"""

import uuid
from decimal import Decimal

from django.core.validators import RegexValidator
from django.db import models, transaction

from apps.core.models import AttributedModel, UUIDTimestampedModel
from apps.core.utils import client_document_upload_path, invoice_upload_path, receipt_upload_path

reference_code_validator = RegexValidator(
    regex=r"^HL-[A-Za-z0-9]+-[A-Za-z]+\d+$",
    message="Reference code must look like 'HL-PSW001-C001'.",
)


class ClientDocumentCategory(models.TextChoices):
    # Stored value "svc_agreement" (was "contract" — renamed to match the
    # client-facing "Service Agreement" label and stop the internal/label
    # mismatch). Reference codes keep the "C" type letter (see services.TYPE_CODES).
    SVC_AGREEMENT = "svc_agreement", "Service Agreement"
    QUOTATION = "quotation", "Quotation"
    WELCOME_BOOKLET = "welcome_booklet", "Welcome Booklet"
    FAQ = "faq", "Frequently Asked Questions"
    OTHER = "other", "Other"


# Categories that carry a reference_code + signed_on (formal, signable records).
SIGNABLE_CATEGORIES = {ClientDocumentCategory.SVC_AGREEMENT, ClientDocumentCategory.QUOTATION}


class ClientDocument(UUIDTimestampedModel, AttributedModel):
    """Service Agreement, Quotation, Welcome Booklet, FAQ, or any other client PDF."""
    engagement = models.ForeignKey(
        "portal.EventEngagement", on_delete=models.CASCADE,
        related_name="client_documents", null=True, blank=True,
    )
    category = models.CharField(max_length=20, choices=ClientDocumentCategory.choices)
    reference_code = models.CharField(
        max_length=50, blank=True, validators=[reference_code_validator],
        help_text="Only used for Service Agreement / Quotation, e.g. 'HL-PSW001-C001'.",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to=client_document_upload_path, max_length=500)
    is_signed = models.BooleanField(default=False)
    signed_on = models.DateField(null=True, blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["order", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["reference_code"],
                condition=models.Q(reference_code__gt=""),
                name="unique_client_document_reference_code",
            )
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_category_display()})"

    def get_portal_id(self):
        try:
            return self.engagement.portal_id
        except AttributeError:
            raise ValueError(
                f"ClientDocument '{self.title}' has no engagement. Cannot generate upload path."
            )


class PaymentMilestoneStatus(models.TextChoices):
    PAID = "paid", "Paid"
    # A real payment that does not cover the whole milestone. It exists because
    # money arrives in amounts the plan did not predict — a client sends a
    # ~3,000,000 retainer against a 2,100,000 deposit, or 1,500,000 against a
    # 2,800,000 phase — and a paid/pending boolean has to round that to one of
    # two lies. Derived from `amount_paid`, never set by hand: see
    # services.derive_milestone_status.
    PART_PAID = "part_paid", "Partly paid"
    PENDING = "pending", "Pending"


class PaymentSchedule(UUIDTimestampedModel, AttributedModel):
    """One per engagement — backs the Payment Overview tiles + milestone tracker."""
    engagement = models.OneToOneField(
        "portal.EventEngagement", on_delete=models.CASCADE, related_name="payment_schedule"
    )
    total_investment = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    def __str__(self) -> str:
        return f"Payment Schedule — {self.engagement.event.title}"

    @property
    def paid_to_date(self):
        """Money actually received against this schedule.

        Sums `amount_paid`, not the full `amount` of milestones flagged paid.
        The old form counted a milestone as all-or-nothing, so a part payment
        contributed zero and the tile under-reported real money; it also meant
        the number could only ever move in whole milestones.
        """
        return self.milestones.aggregate(total=models.Sum("amount_paid"))["total"] or 0

    @property
    def remaining_balance(self):
        return self.total_investment - self.paid_to_date

    @property
    def next_payment_milestone(self):
        """Earliest milestone still owing anything — drives "Next Payment Due".

        Excludes PAID rather than requiring PENDING: a part-paid milestone is
        still the next thing owed, and filtering on PENDING alone would skip
        past it to a milestone that has had nothing paid against it at all.
        """
        return (
            self.milestones.exclude(status=PaymentMilestoneStatus.PAID)
            .order_by(models.F("due_date").asc(nulls_last=True), "order")
            .first()
        )


class PaymentMilestone(UUIDTimestampedModel, AttributedModel):
    """One row per milestone in the tracker (Deposit / Phase 2 / Final Payment, ...)."""
    schedule = models.ForeignKey(PaymentSchedule, on_delete=models.CASCADE, related_name="milestones")
    label = models.CharField(max_length=100)  # "Deposit", "Phase 2", "Final Payment"
    # Share of the contract's total_investment. The percentage is the source of
    # truth: amount is derived as percentage/100 * total_investment and re-derived
    # whenever the total changes (services.recompute_milestone_amounts). A
    # schedule's percentage-based milestones must sum to 100. Nullable so ad-hoc
    # one-off milestones (add_milestone) and any legacy rows still work — those
    # carry a fixed amount and sit outside the sum-to-100 rule.
    percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Share of total_investment; a schedule's percentage-based milestones must sum to 100.",
    )
    # default=0 so a new admin-inline row (where amount is derived, not typed) can
    # save before PaymentScheduleAdmin.save_related recomputes it from percentage.
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    # How much of `amount` has actually been received. DERIVED, not typed:
    # services.sync_milestone_from_invoices recomputes it from the milestone's
    # paid invoices, which is what makes an invoice the thing that drives this
    # schedule. A milestone with no invoices keeps whatever mark_milestone_paid
    # last set, so ad-hoc and legacy rows still work.
    amount_paid = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    due_date = models.DateField(null=True, blank=True)
    paid_on = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=10, choices=PaymentMilestoneStatus.choices, default=PaymentMilestoneStatus.PENDING
    )
    order = models.IntegerField(default=0)
    # Set by document_hub.tasks.payment_due_digest_task the first time it
    # emails about this milestone — prevents re-sending the digest every day
    # while it's still pending and within the lookahead window.
    reminder_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order", "due_date"]

    def __str__(self) -> str:
        return f"{self.label} — {self.amount_paid}/{self.amount} ({self.status})"

    @property
    def balance(self):
        """Still owed on this milestone. Never negative — an overpayment (the
        client rounds up, or a retainer exceeds the deposit) settles the
        milestone and leaves nothing owing rather than reading as a credit the
        tracker has no way to carry forward."""
        return max(self.amount - self.amount_paid, Decimal("0"))


class InvoiceStatus(models.TextChoices):
    PAID = "paid", "Paid"
    PENDING = "pending", "Pending"


class Invoice(UUIDTimestampedModel, AttributedModel):
    """One row per issued invoice in the Invoices table."""
    engagement = models.ForeignKey(
        "portal.EventEngagement", on_delete=models.CASCADE, related_name="invoices"
    )
    # The milestone this invoice bills for. Nullable because an invoice can be
    # raised for something outside the schedule entirely, and because every
    # invoice written before this field existed has none. When set, paying this
    # invoice is what moves the milestone — see
    # services.sync_milestone_from_invoices.
    #
    # A ForeignKey, not a OneToOne: a milestone can legitimately be billed in
    # more than one instalment, and the sync sums whichever of them are paid.
    # SET_NULL so deleting a milestone never destroys an issued invoice — that
    # is a client-facing numbered record.
    milestone = models.ForeignKey(
        "PaymentMilestone", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="invoices",
    )
    invoice_number = models.CharField(max_length=50, unique=True, validators=[reference_code_validator])
    issued_on = models.DateField()
    # Nullable ONLY for the auto-issued case: an invoice raised alongside a
    # milestone that has no agreed due date yet has no honest date to carry, and
    # inventing "today" would show the client an invoice already overdue. The
    # serializer still requires it on manual creation, so nothing an API caller
    # writes can omit it.
    due_on = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    status = models.CharField(max_length=10, choices=InvoiceStatus.choices, default=InvoiceStatus.PENDING)
    file = models.FileField(upload_to=invoice_upload_path, blank=True, null=True, max_length=500)

    class Meta:
        ordering = ["-issued_on"]

    def __str__(self) -> str:
        return f"{self.invoice_number} ({self.status})"

    def get_portal_id(self):
        try:
            return self.engagement.portal_id
        except AttributeError:
            raise ValueError(f"Invoice '{self.invoice_number}' has no engagement. Cannot generate upload path.")


class Receipt(UUIDTimestampedModel, AttributedModel):
    """One row per completed payment receipt in the Receipts table."""
    engagement = models.ForeignKey(
        "portal.EventEngagement", on_delete=models.CASCADE, related_name="receipts"
    )
    receipt_number = models.CharField(max_length=50, unique=True, validators=[reference_code_validator])
    paid_on = models.DateField()
    payment_for = models.CharField(max_length=255)  # "Non-refundable Retainer"
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    file = models.FileField(upload_to=receipt_upload_path, blank=True, null=True, max_length=500)

    class Meta:
        ordering = ["-paid_on"]

    def __str__(self) -> str:
        return f"{self.receipt_number} — {self.payment_for}"

    def get_portal_id(self):
        try:
            return self.engagement.portal_id
        except AttributeError:
            raise ValueError(f"Receipt '{self.receipt_number}' has no engagement. Cannot generate upload path.")


class PortalDefaults(UUIDTimestampedModel):
    """
    The single, global config staff fill in ONCE for the boilerplate content
    every client portal needs. When an EventEngagement is created its Document
    Hub is auto-seeded with a copy of the FAQ template only (the one document
    that's identical for every client), and a new ClientPortal's welcome_message
    is seeded from `welcome_message` — see services.seed_engagement_documents /
    apply_default_welcome_message, wired up in signals.py.

    The service_agreement_file / welcome_booklet_file slots below remain
    configurable but are NOT auto-cloned: a Service Agreement is a per-client
    legal document and the Welcome Booklet differs per client, so staff attach
    those (and the Quotation) per engagement via create_document.

    This is a singleton: every row is pinned to one fixed primary key, so
    `save()` can only ever write that one row and `load()` is a get-or-create on
    it. Files are stored once under portal_defaults/ and cloned per client (the
    client gets an independent copy, not a shared blob).
    """
    # Fixed PK so there is exactly one row — save() pins to it, load() targets it.
    SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

    service_agreement_file = models.FileField(
        upload_to="portal_defaults/service_agreement/", max_length=500, null=True, blank=True,
    )
    welcome_booklet_file = models.FileField(
        upload_to="portal_defaults/welcome_booklet/", max_length=500, null=True, blank=True,
    )
    faq_file = models.FileField(
        upload_to="portal_defaults/faq/", max_length=500, null=True, blank=True,
    )
    welcome_message = models.TextField(blank=True)

    class Meta:
        verbose_name = "Portal Defaults"
        verbose_name_plural = "Portal Defaults"

    def __str__(self) -> str:
        return "Portal Defaults"

    def save(self, *args, **kwargs):
        # Pin every write to the one fixed PK so a second row can't exist.
        self.pk = self.SINGLETON_ID
        self.id = self.SINGLETON_ID
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "PortalDefaults":
        """Return the singleton, creating an empty one on first access."""
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_ID)
        return obj


class ReferenceCounter(models.Model):
    """
    A named, monotonically-increasing counter backing auto-generated reference
    codes (services.next_reference_code). One row per `scope`:

      * "engagement"            — the global sequence for the PSW### engagement
                                  segment (PSW001, PSW002, …).
      * "<engagement_pk>:<TYPE>" — the per-engagement, per-type suffix (so each
                                  engagement's invoices restart at INV001, etc.).

    `next()` increments under select_for_update so concurrent creates can't
    collide on a number, and — being a plain DB write — rolls back with the
    surrounding transaction, so a failed insert doesn't burn a value.
    """
    scope = models.CharField(max_length=255, unique=True)
    value = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.scope} = {self.value}"

    @classmethod
    def next(cls, scope: str) -> int:
        with transaction.atomic():
            counter, _ = cls.objects.select_for_update().get_or_create(scope=scope)
            counter.value += 1
            counter.save(update_fields=["value"])
            return counter.value
