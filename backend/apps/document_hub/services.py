"""
apps/document_hub/services.py
"""

from __future__ import annotations

import os
from decimal import ROUND_HALF_UP, Decimal

from django.core.files.base import ContentFile

from apps.core.utils import stamp_attribution
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

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
    PaymentMilestone.objects.bulk_create([
        PaymentMilestone(
            schedule=schedule, label=label, percentage=Decimal(str(pct)), amount=amt, order=i,
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
    service agreement, quotation, welcome/service PDFs, payment overview
    (schedule may be absent if staff hasn't set one up yet), invoices, receipts.
    """
    if engagement is None:
        return {
            "service_agreement": None,
            "quotation": None,
            "welcome_service_info": [],
            "payment_schedule": None,
            "invoices": [],
            "receipts": [],
        }

    documents = engagement.client_documents.all()

    return {
        "service_agreement": documents.filter(category=ClientDocumentCategory.SVC_AGREEMENT).first(),
        "quotation": documents.filter(category=ClientDocumentCategory.QUOTATION).first(),
        "welcome_service_info": list(
            documents.exclude(
                category__in=[ClientDocumentCategory.SVC_AGREEMENT, ClientDocumentCategory.QUOTATION]
            )
        ),
        "payment_schedule": getattr(engagement, "payment_schedule", None),
        "invoices": list(engagement.invoices.all()),
        "receipts": list(engagement.receipts.all()),
    }


def mark_milestone_paid(milestone: PaymentMilestone, paid_on=None, updated_by=None) -> PaymentMilestone:
    """Staff marks a milestone as paid — stamps paid_on (defaults to today)."""
    from .models import PaymentMilestoneStatus

    milestone.status = PaymentMilestoneStatus.PAID
    milestone.paid_on = paid_on or timezone.now().date()
    update_fields = ["status", "paid_on"]
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
