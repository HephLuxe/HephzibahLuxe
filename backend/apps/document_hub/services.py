"""
apps/document_hub/services.py
"""

from __future__ import annotations

import os
from decimal import ROUND_HALF_UP, Decimal

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.utils import stamp_attribution

from .models import (
    ClientDocument,
    ClientDocumentCategory,
    PaymentMilestone,
    PortalDefaults,
    ReferenceCounter,
)

# ── Contract payment split ───────────────────────────────────────
#
# A payment schedule is split into phases by *percentage* of the contract's
# total_investment — the percentage is the source of truth, the amount is
# derived (see recompute_milestone_amounts). The default contract structure is
# 30% deposit / 40% mid / 30% final; edit DEFAULT_PAYMENT_SPLIT to shift the
# default for all NEW schedules. Per-client overrides go through the Django
# admin (edit the percentages inline) or generate_milestones().
#
# Each entry is (label, percentage); percentages must sum to exactly 100.
DEFAULT_PAYMENT_SPLIT: list[tuple[str, Decimal]] = [
    ("Deposit", Decimal("30")),
    ("Phase 2", Decimal("40")),
    ("Final Payment", Decimal("30")),
]

_CENTS = Decimal("0.01")


def validate_split(split: list[tuple[str, Decimal]]) -> None:
    """Raise ValidationError unless the split has entries summing to exactly 100%."""
    if not split:
        raise ValidationError("A payment split needs at least one milestone.")
    total = sum((Decimal(str(pct)) for _, pct in split), Decimal("0"))
    if total != Decimal("100"):
        raise ValidationError(f"Payment split percentages must sum to 100 (got {total}).")


def _amounts_from_split(total: Decimal, percentages: list[Decimal]) -> list[Decimal]:
    """
    Money amount for each percentage of `total`. The LAST amount absorbs the
    rounding remainder so the amounts sum *exactly* to `total` (e.g. a 33/33/34
    split of 100.00 yields 33.00 / 33.00 / 34.00, not 32.99). Assumes the
    percentages sum to 100 — callers validate that first.
    """
    amounts: list[Decimal] = []
    running = Decimal("0")
    for pct in percentages[:-1]:
        amt = (total * Decimal(str(pct)) / Decimal("100")).quantize(_CENTS, ROUND_HALF_UP)
        amounts.append(amt)
        running += amt
    amounts.append((total - running).quantize(_CENTS, ROUND_HALF_UP))
    return amounts


@transaction.atomic
def generate_milestones(schedule, split: list[tuple[str, Decimal]] | None = None) -> list[PaymentMilestone]:
    """
    (Re)build a schedule's milestones from a percentage split (default 30/40/30).
    Replaces ALL existing milestones for the schedule. Amounts are derived from
    the schedule's current total_investment. Used at schedule creation and by the
    admin "Reset to default split" action.
    """
    split = split if split is not None else DEFAULT_PAYMENT_SPLIT
    validate_split(split)

    schedule.milestones.all().delete()
    amounts = _amounts_from_split(schedule.total_investment, [pct for _, pct in split])
    # Inherit the schedule's creator. These rows are generated, never typed, so
    # there is no request.user at this layer — and left unstamped they came back
    # with an empty created_by_display, which reads as "nobody made this" for
    # rows that plainly had an author. Same inheritance the auto-created
    # ClientPortal uses (apps/portal/signals.py).
    PaymentMilestone.objects.bulk_create([
        PaymentMilestone(
            schedule=schedule, label=label, percentage=Decimal(str(pct)), amount=amt, order=i,
            created_by=schedule.created_by,
        )
        for i, ((label, pct), amt) in enumerate(zip(split, amounts))
    ])
    return list(schedule.milestones.all())


def recompute_milestone_amounts(schedule) -> None:
    """
    Re-derive amounts for the schedule's percentage-based milestones from the
    current total_investment — call after total_investment changes, or after an
    admin edits the percentages. Ad-hoc milestones (percentage is NULL) carry a
    fixed amount and are left untouched. Preserves exact-sum across the
    percentage-based milestones (their percentages are assumed to sum to 100).
    """
    pct_milestones = list(schedule.milestones.exclude(percentage__isnull=True).order_by("order"))
    if not pct_milestones:
        return
    amounts = _amounts_from_split(schedule.total_investment, [m.percentage for m in pct_milestones])
    for milestone, amount in zip(pct_milestones, amounts):
        if milestone.amount != amount:
            milestone.amount = amount
            milestone.save(update_fields=["amount"])


def build_hub(engagement) -> dict:
    """
    Assemble the full "HL Client Document Hub" page in one shape:
    service agreements, quotations, welcome/service PDFs, payment overview
    (schedule may be absent if staff hasn't set one up yet), invoices, receipts.

    The two signable categories are LISTS, like every other collection here.
    They were singular (`.first()`) until a staff member uploaded a revised
    quotation and the hub silently showed only one of the two — the write path
    has always allowed several per engagement, and `next_reference_code` numbers
    them C001/C002, Q001/Q002 for exactly that reason. A revision is the normal
    case for a quotation, so the read path now matches: every one is returned,
    newest first (Meta.ordering is ["order", "-created_at"]), and the client
    decides what to show.
    """
    if engagement is None:
        return {
            "service_agreements": [],
            "quotations": [],
            "welcome_service_info": [],
            "payment_schedule": None,
            "invoices": [],
            "receipts": [],
        }

    documents = engagement.client_documents.all()

    return {
        "service_agreements": list(
            documents.filter(category=ClientDocumentCategory.SVC_AGREEMENT)
        ),
        "quotations": list(documents.filter(category=ClientDocumentCategory.QUOTATION)),
        "welcome_service_info": list(
            documents.exclude(
                category__in=[ClientDocumentCategory.SVC_AGREEMENT, ClientDocumentCategory.QUOTATION]
            )
        ),
        "payment_schedule": getattr(engagement, "payment_schedule", None),
        "invoices": list(engagement.invoices.all()),
        "receipts": list(engagement.receipts.all()),
    }


# ── Invoices drive the payment schedule ──────────────────────────
#
# There used to be no relationship at all between the two: `Invoice` had one FK
# (engagement), and `paid_to_date` summed milestones. So staff issued three
# invoices mirroring three milestones, flipped an invoice to paid, and the
# Payment Overview did not move — the flip wrote a column nothing downstream
# read. The only way to move the tiles was to *also* mark the milestone paid,
# which is the same fact entered twice, with nothing keeping the two entries
# honest.
#
# The direction is now one-way and explicit: an invoice is the billing record,
# and money recorded against an invoice is what a milestone reflects.
# `PaymentMilestone.amount_paid` and `.status` are DERIVED from the milestone's
# invoices and should not be written by hand anywhere else.


def derive_milestone_status(milestone: PaymentMilestone) -> str:
    """The status implied by how much has been paid. The single definition of
    what paid/part-paid/pending mean, so the API, the admin and the digest task
    can never disagree about a milestone's state."""
    from .models import PaymentMilestoneStatus

    if milestone.amount_paid <= 0:
        return PaymentMilestoneStatus.PENDING
    # `>=`, not `==`: an overpayment settles the milestone. See
    # PaymentMilestone.balance for why the excess is not carried forward.
    if milestone.amount_paid >= milestone.amount:
        return PaymentMilestoneStatus.PAID
    return PaymentMilestoneStatus.PART_PAID


def sync_milestone_from_invoices(milestone: PaymentMilestone) -> PaymentMilestone:
    """
    Re-derive one milestone's `amount_paid` / `status` / `paid_on` from its paid
    invoices, and email the client if it just became fully paid.

    Recomputes from the full set rather than adding a delta, which makes it
    idempotent: unpaying an invoice, deleting one, editing an amount and
    re-running all converge on the same answer. A delta would drift the first
    time any of those happened.

    Call this ONLY for a milestone that is invoice-driven. It derives strictly
    from the invoices, so a milestone with none is reset to zero — which is
    correct when its last invoice was just deleted (the money went with it), and
    wrong for a milestone that never had one. `mark_milestone_paid` is what
    branches on that; it writes ad-hoc and pre-link milestones directly and
    never routes them through here.
    """
    from .models import InvoiceStatus, PaymentMilestoneStatus

    paid_invoices = milestone.invoices.filter(status=InvoiceStatus.PAID)
    was_paid = milestone.status == PaymentMilestoneStatus.PAID

    milestone.amount_paid = paid_invoices.aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0")
    milestone.status = derive_milestone_status(milestone)
    # The date of the LAST payment that settled it — the client's question is
    # "when was this cleared", not "when did the first instalment land". Cleared
    # again if the milestone falls back out of paid, so a reversed invoice does
    # not leave a paid date on an unpaid milestone.
    milestone.paid_on = (
        paid_invoices.order_by("-issued_on").values_list("issued_on", flat=True).first()
        if milestone.status == PaymentMilestoneStatus.PAID
        else None
    )
    milestone.save(update_fields=["amount_paid", "status", "paid_on", "updated_at"])

    # Only on the pending/part-paid -> paid EDGE. Without that guard, editing an
    # unrelated field on an already-paid invoice would re-email the client that
    # the same milestone had been paid.
    if not was_paid and milestone.status == PaymentMilestoneStatus.PAID:
        notify_milestone_paid(milestone)
    return milestone


def sync_invoice_milestone(invoice) -> None:
    """Push an invoice's state onto the milestone it bills for. The one hook
    every invoice write path calls; a no-op for an unlinked invoice."""
    if invoice.milestone_id is None:
        return
    invoice.milestone.refresh_from_db()
    sync_milestone_from_invoices(invoice.milestone)


@transaction.atomic
def issue_invoices_for_schedule(schedule, issued_by=None) -> list:
    """
    Raise one invoice per milestone that has none yet, already linked.

    This is what removes the double entry: setting a total investment produces
    the milestones AND the invoices that bill for them in a single action, so
    staff only ever touch the invoice afterward. Milestones that already have an
    invoice are skipped, so this is safe to re-run after adding a milestone.

    `due_on` is copied from the milestone's `due_date`, which is usually unset at
    this point — deliberately left NULL rather than defaulted to today, since an
    invented due date shows the client an invoice that is already overdue.
    Setting the milestone's due date later fills it (see propagate_due_date).
    """
    from .models import Invoice

    invoices = []
    # `amount_paid=0` alongside "has no invoice": a milestone already settled by
    # hand (mark_milestone_paid before it had any invoice) must not be billed
    # again — and issuing a pending invoice against it would make the next sync
    # recompute it back to unpaid, silently erasing a recorded payment.
    candidates = schedule.milestones.filter(
        invoices__isnull=True, amount_paid__lte=0
    ).order_by("order")
    for milestone in candidates:
        invoices.append(
            Invoice.objects.create(
                engagement=schedule.engagement,
                milestone=milestone,
                issued_on=timezone.now().date(),
                due_on=milestone.due_date,
                amount=milestone.amount,
                # Same inheritance generate_milestones uses: these rows are
                # generated, not typed, so there is no request.user here and an
                # unstamped row reads as "nobody made this".
                created_by=issued_by or schedule.created_by,
            )
        )
    return invoices


def propagate_due_date(milestone: PaymentMilestone) -> None:
    """Carry a milestone's due date onto the invoices billing for it.

    Only fills invoices that have no date of their own — a due date a staff
    member typed onto an invoice is the one the client was sent and outranks the
    plan.
    """
    if milestone.due_date is None:
        return
    milestone.invoices.filter(due_on__isnull=True).update(due_on=milestone.due_date)


def mark_milestone_paid(milestone: PaymentMilestone, paid_on=None, updated_by=None) -> PaymentMilestone:
    """Staff marks a milestone as paid — stamps paid_on (defaults to today).

    When the milestone HAS invoices, this marks those invoices paid and lets the
    sync settle the milestone, rather than writing the milestone directly. Same
    outcome for the caller, but it keeps one source of truth: writing
    `amount_paid` here would leave the invoices saying "pending", and the next
    invoice edit would recompute the milestone straight back to unpaid.

    Unlinked milestones (ad-hoc, or predating the link) are still written
    directly — there is nothing else to record the payment on.
    """
    from .models import InvoiceStatus, PaymentMilestoneStatus

    paid_on = paid_on or timezone.now().date()

    if milestone.invoices.exists():
        milestone.invoices.exclude(status=InvoiceStatus.PAID).update(
            status=InvoiceStatus.PAID
        )
        return sync_milestone_from_invoices(milestone)

    milestone.status = PaymentMilestoneStatus.PAID
    milestone.amount_paid = milestone.amount
    milestone.paid_on = paid_on
    update_fields = ["status", "amount_paid", "paid_on"]
    if stamp_attribution(milestone, updated_by, creating=False):
        update_fields.append("last_updated_by")
    milestone.save(update_fields=update_fields)
    notify_milestone_paid(milestone)
    return milestone


# ── Client notifications (each gated by its own NotificationTypeSettings row —
# see apps/notifications/README.md) ─────────────────────────────────────────
#
# Deliberately NOT fired from seed_engagement_documents: those documents are
# boilerplate cloned the moment an engagement is created, often before the
# client has even completed their first login — emailing "a new document was
# added" at that instant would just be noise alongside the separate welcome/
# credentials email. Only documents/invoices/receipts a staff member actively
# creates afterward, and milestones staff mark paid, notify the client.

def notify_document_added(document: ClientDocument) -> None:
    from apps.notifications.services import queue_notification

    engagement = document.engagement
    if engagement is None:
        return
    portal = engagement.portal
    queue_notification(
        recipient_email=portal.user.email,
        recipient_user=portal.user,
        engagement=engagement,
        template_name="document_added",
        context={
            "document_title": document.title,
            "category_display": document.get_category_display(),
            "event_title": engagement.event.title if engagement.event else "",
        },
    )


def notify_invoice_issued(invoice) -> None:
    from apps.notifications.services import queue_notification

    engagement = invoice.engagement
    portal = engagement.portal
    queue_notification(
        recipient_email=portal.user.email,
        recipient_user=portal.user,
        engagement=engagement,
        template_name="invoice_issued",
        context={
            "invoice_number": invoice.invoice_number,
            "amount": str(invoice.amount),
            "due_on": str(invoice.due_on),
            "event_title": engagement.event.title if engagement.event else "",
        },
    )


def notify_receipt_issued(receipt) -> None:
    from apps.notifications.services import queue_notification

    engagement = receipt.engagement
    portal = engagement.portal
    queue_notification(
        recipient_email=portal.user.email,
        recipient_user=portal.user,
        engagement=engagement,
        template_name="receipt_issued",
        context={
            "receipt_number": receipt.receipt_number,
            "amount": str(receipt.amount),
            "payment_for": receipt.payment_for,
            "event_title": engagement.event.title if engagement.event else "",
        },
    )


def notify_milestone_paid(milestone: PaymentMilestone) -> None:
    from apps.notifications.services import queue_notification

    engagement = milestone.schedule.engagement
    portal = engagement.portal
    queue_notification(
        recipient_email=portal.user.email,
        recipient_user=portal.user,
        engagement=engagement,
        template_name="milestone_paid",
        context={
            "label": milestone.label,
            "amount": str(milestone.amount),
            "paid_on": str(milestone.paid_on),
            "event_title": engagement.event.title if engagement.event else "",
        },
    )


# ── Auto-seeded portal defaults ──────────────────────────────────
#
# Every new engagement's Document Hub is seeded with copies of the boilerplate
# documents staff configure once on PortalDefaults; every new portal gets the
# default welcome message. Wired to model creation in signals.py.
#
# Each entry: PortalDefaults file attribute -> (category, default title, order).
# Only the FAQ is boilerplate shared across every client. The Service Agreement
# and Welcome Booklet are per-client (as is the Quotation, which is created via
# the API), so they are intentionally NOT auto-seeded — staff attach them per
# engagement through create_document (POST /document-hub/documents/). Their
# upload slots on PortalDefaults are kept but no longer cloned on creation.
SEEDED_DOC_SPECS = [
    ("faq_file", ClientDocumentCategory.FAQ, "Frequently Asked Questions", 0),
]


def seed_engagement_documents(engagement) -> list[ClientDocument]:
    """
    Populate an engagement's Document Hub with copies of the configured default
    documents. Idempotent and skip-safe:

      * a slot with no template file configured is skipped (a ClientDocument
        requires a file);
      * a category the engagement already has is skipped (so a staff-deleted
        doc is never silently re-added, and re-runs don't duplicate).

    The file bytes are CLONED onto each engagement's own ClientDocument (not
    shared) — the same pattern contacts.copy_contacts_from_day uses. The
    ClientDocument is created with its engagement first, because the upload path
    (core.utils.client_document_upload_path) reads engagement.portal_id before
    the file can be saved.

    Only the FAQ is seeded (see SEEDED_DOC_SPECS) — it carries no reference_code.
    The signable documents (Service Agreement / Quotation) are added per-client
    via create_document, which is where their HL-…-C001 / -Q001 codes get filled.
    """
    defaults = PortalDefaults.load()
    created: list[ClientDocument] = []

    for attr, category, title, order in SEEDED_DOC_SPECS:
        template = getattr(defaults, attr, None)
        if not template:
            continue
        if engagement.client_documents.filter(category=category).exists():
            continue

        document = ClientDocument(
            engagement=engagement,
            category=category,
            title=title,
            order=order,
            reference_code="",
            is_signed=False,
        )
        # .save() on the file field re-runs the upload path for THIS engagement
        # and persists the model, producing an independent copy of the bytes.
        template.open("rb")
        try:
            document.file.save(os.path.basename(template.name), ContentFile(template.read()), save=True)
        finally:
            template.close()
        created.append(document)

    return created


def apply_default_welcome_message(portal) -> None:
    """
    Seed a portal's welcome_message from PortalDefaults on creation — only when
    the portal has none yet (never overwrite a staff-typed message) and only
    when a default is configured.
    """
    if portal.welcome_message:
        return
    default = PortalDefaults.load().welcome_message
    if default:
        portal.welcome_message = default
        portal.save(update_fields=["welcome_message", "updated_at"])


# ── Auto-generated reference codes ───────────────────────────────
#
# Every Document Hub reference code reads HL-<segment>-<TYPE><NNN>, e.g.
# HL-PSW006-INV001. The <segment> (e.g. PSW006) is assigned once, on first need,
# and frozen thereafter. It encodes:
#   <II>   two initials from the event's names — Wedding: bride + groom
#          (e.g. Priscilla & Samuel -> "PS"); Birthday: honoree_name; other
#          types: event_name. See _segment_initials / _two_letters.
#   <CODE> the event-type letter (Wedding->W, Birthday->B, Corporate->C,
#          Social Events->S, Others->O).
#   <NNN>  how many of THAT event type the business has ever done — a global
#          per-event-type counter (the 6th wedding -> 006).
# The trailing <NNN> on the full code (e.g. INV001) is separate: it restarts at
# 001 per engagement per type. All system-generated and read-only; staff never
# type these. Filled by pre_save signals so every path (API, admin, seed, shell)
# is covered.

REFERENCE_PREFIX = "HL"

# Event-type -> single-letter segment code.
EVENT_TYPE_CODES = {
    "Wedding": "W",
    "Birthday": "B",
    "Corporate": "C",
    "Social Events": "S",
    "Others": "O",
}

# Reference type letters per record. ClientDocument keys on its category; Invoice
# and Receipt key on the string "invoice"/"receipt".
TYPE_CODES = {
    ClientDocumentCategory.SVC_AGREEMENT: "C",
    ClientDocumentCategory.QUOTATION: "Q",
    "invoice": "INV",
    "receipt": "R",
}


def _event_code(event) -> str:
    return EVENT_TYPE_CODES.get((event.event_type or "").strip(), "")


def _two_letters(text: str) -> str:
    """Two initials from a name. 2+ words -> first letter of the first two words
    ('Tola Obi' -> 'TO'); a single word -> its first two letters ('Acme' -> 'AC').
    No filler."""
    tokens = (text or "").split()
    raw = (tokens[0][:1] + tokens[1][:1]) if len(tokens) >= 2 else (tokens[0][:2] if tokens else "")
    return "".join(c for c in raw if c.isalpha()).upper()[:2]


def _segment_initials(event) -> str:
    """Two uppercase initials for the segment.
    Wedding : bride initial + groom initial (bride first, per the PS example).
    Birthday: two letters from honoree_name.
    Corporate/Social/Others: two letters from event_name (falls back to title)."""
    et = (event.event_type or "").strip()
    if et == "Wedding":
        raw = (event.bride_name or "")[:1] + (event.groom_name or "")[:1]
        return "".join(c for c in raw if c.isalpha()).upper()[:2]
    if et == "Birthday":
        return _two_letters(event.honoree_name)
    return _two_letters(event.event_name or event.title)


def assign_engagement_segment(engagement) -> str:
    """Ensure the engagement has its <II><CODE><NNN> segment (e.g. PSW006),
    deriving it from the event's names and type on first need. Idempotent —
    assigned once, then frozen. <NNN> is a global per-event-type counter."""
    if engagement.reference_segment:
        return engagement.reference_segment
    event = engagement.event
    code = _event_code(event)
    number = ReferenceCounter.next(f"eventtype:{code}")   # 6th wedding -> 6
    engagement.reference_segment = f"{_segment_initials(event)}{code}{number:03d}"
    engagement.save(update_fields=["reference_segment"])
    return engagement.reference_segment


def next_reference_code(engagement, type_code: str) -> str:
    """
    The next code for a record type on an engagement, e.g.
    next_reference_code(eng, "INV") -> "HL-PSW006-INV002". Assigns the
    engagement's segment first if it doesn't have one yet.
    """
    segment = assign_engagement_segment(engagement)
    number = ReferenceCounter.next(f"{engagement.pk}:{type_code}")
    return f"{REFERENCE_PREFIX}-{segment}-{type_code}{number:03d}"
