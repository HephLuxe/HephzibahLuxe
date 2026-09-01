"""
apps/document_hub/views.py

Routes (all under /api/v1/):
  GET    /document-hub/                                   client (own) / staff (?portal_id=)
  POST   /document-hub/documents/                          staff
  PATCH·DELETE /document-hub/documents/<uuid>/             staff
  POST   /document-hub/payment-schedule/                   staff
  PATCH  /document-hub/payment-schedule/<uuid>/            staff
  POST   /document-hub/payment-schedule/<uuid>/milestones/ staff
  PATCH·DELETE /document-hub/milestones/<uuid>/            staff
  PATCH  /document-hub/milestones/<uuid>/mark-paid/        staff
  POST   /document-hub/invoices/                           staff
  PATCH·DELETE /document-hub/invoices/<uuid>/               staff
  POST   /document-hub/receipts/                            staff
  PATCH·DELETE /document-hub/receipts/<uuid>/                staff
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.error_codes import (
    CONFIRMATION_REQUIRED,
    NOT_FOUND,
    PERMISSION_DENIED,
    VALIDATION_ERROR,
)
from apps.core.permissions import IsStaffOrSuperuser, is_staff_or_superuser
from apps.core.utils import parse_decimal, save_with_attribution
from apps.portal.models import ClientPortal

from . import services
from .models import (
    ClientDocument,
    Invoice,
    PaymentMilestone,
    PaymentMilestoneStatus,
    PaymentSchedule,
    PortalDefaults,
    Receipt,
)
from .serializers import (
    ClientDocumentSerializer,
    InvoiceSerializer,
    PaymentMilestoneSerializer,
    PaymentScheduleSerializer,
    PortalDefaultsSerializer,
    ReceiptSerializer,
)

# ── Envelope helper (see apps/core/exceptions.py for the exception-path half) ──

def _error(detail: str, code: str, http_status: int, errors: dict | None = None) -> Response:
    body: dict = {"detail": detail, "code": code}
    if errors:
        body["errors"] = errors
    return Response(body, status=http_status)


def _resolve_portal(request: Request):
    """Client -> their own portal. Staff -> any portal via ?portal_id=<uuid>."""
    portal_id = request.query_params.get("portal_id")
    if portal_id:
        if not is_staff_or_superuser(request.user):
            return None, _error("Only staff can view other clients' documents.", PERMISSION_DENIED, 403)
        try:
            portal = ClientPortal.objects.filter(id=portal_id).first()
        except (ValueError, DjangoValidationError):
            return None, _error("Invalid portal_id.", VALIDATION_ERROR, 400)
        if portal is None:
            return None, _error("Portal not found.", NOT_FOUND, 404)
        return portal, None

    portal = ClientPortal.objects.filter(user=request.user).first()
    if portal is None:
        return None, _error("No portal found for this user.", NOT_FOUND, 404)
    return portal, None


def _resolve_portal_by_id(portal_id):
    """Staff write helper: portal_id required in body. Returns (portal, None) or (None, error)."""
    if not portal_id:
        return None, _error("portal_id is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)
    try:
        portal = ClientPortal.objects.filter(id=portal_id).first()
    except (ValueError, DjangoValidationError):
        return None, _error("Invalid portal_id.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)
    if portal is None:
        return None, _error("Portal not found.", NOT_FOUND, status.HTTP_404_NOT_FOUND)
    return portal, None


def _resolve_active_engagement(portal, engagement_id=None):
    """
    Returns (engagement, None) or (None, error).
    Without engagement_id: the portal's active engagement (previous behavior).
    With engagement_id: that specific engagement, validated to belong to this
    portal — lets staff pre-stage a future event's documents/invoices/receipts
    before switching to it (see docs/FAILURE_POINTS_AUDIT.md F3 addendum).
    """
    if engagement_id:
        engagement = portal.engagements.filter(id=engagement_id).first()
        if engagement is None:
            return None, _error("Engagement not found for this portal.", NOT_FOUND, status.HTTP_404_NOT_FOUND)
        return engagement, None

    engagement = portal.active_engagement
    if engagement is None:
        return None, _error("This portal has no active engagement.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)
    return engagement, None


# ── Hub aggregate ────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_hub(request: Request) -> Response:
    """The full Document Hub page for the portal's active engagement."""
    portal, err = _resolve_portal(request)
    if err:
        return err

    hub = services.build_hub(portal.active_engagement)
    return Response({
        "service_agreements": ClientDocumentSerializer(hub["service_agreements"], many=True).data,
        "quotations": ClientDocumentSerializer(hub["quotations"], many=True).data,
        "welcome_service_info": ClientDocumentSerializer(hub["welcome_service_info"], many=True).data,
        "payment_schedule": PaymentScheduleSerializer(hub["payment_schedule"]).data if hub["payment_schedule"] else None,
        "invoices": InvoiceSerializer(hub["invoices"], many=True).data,
        "receipts": ReceiptSerializer(hub["receipts"], many=True).data,
    })


# ── Client documents (Service Agreement / Quotation / Welcome & Service Info) ──

@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def create_document(request: Request) -> Response:
    """Staff only. Body: portal_id + ClientDocument fields."""
    portal, err = _resolve_portal_by_id(request.data.get("portal_id"))
    if err:
        return err
    engagement, err = _resolve_active_engagement(portal, request.data.get("engagement_id"))
    if err:
        return err

    serializer = ClientDocumentSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid document data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    document = save_with_attribution(serializer, request.user, engagement=engagement)
    services.notify_document_added(document)
    return Response(ClientDocumentSerializer(document).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def document_detail(request: Request, document_id: str) -> Response:
    document = ClientDocument.objects.filter(id=document_id).first()
    if document is None:
        return _error("Document not found.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        document.delete()
        return Response({"detail": "Document deleted."})

    serializer = ClientDocumentSerializer(document, data=request.data, partial=True)
    if not serializer.is_valid():
        return _error("Invalid document data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user)
    return Response(serializer.data)


# ── Portal defaults (org-wide templates auto-seeded to every client) ──

@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def portal_defaults(request: Request) -> Response:
    """
    Staff only. Manage the single global defaults record — the Service
    Agreement / Welcome Booklet / FAQ template files + the default welcome
    message that every new engagement/portal is seeded from.

    GET   — read the current defaults.
    PATCH — replace any of the three files (multipart) and/or the welcome
            message. Only new clients created afterward are affected; existing
            clients' already-seeded documents are untouched.
    """
    defaults = PortalDefaults.load()

    if request.method == "GET":
        return Response(PortalDefaultsSerializer(defaults, context={"request": request}).data)

    serializer = PortalDefaultsSerializer(
        defaults, data=request.data, partial=True, context={"request": request},
    )
    if not serializer.is_valid():
        return _error("Invalid defaults data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)
    save_with_attribution(serializer, request.user)
    return Response(serializer.data)


# ── Payment schedule & milestones ───────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def create_payment_schedule(request: Request) -> Response:
    """Staff only. Body: portal_id + total_investment. One schedule per engagement."""
    portal, err = _resolve_portal_by_id(request.data.get("portal_id"))
    if err:
        return err
    engagement, err = _resolve_active_engagement(portal, request.data.get("engagement_id"))
    if err:
        return err

    if PaymentSchedule.objects.filter(engagement=engagement).exists():
        return _error("A payment schedule already exists for this engagement.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    total_investment = parse_decimal(request.data.get("total_investment", 0))
    if total_investment is None:
        return _error("total_investment must be a valid number.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        # created_by only — creating is not editing. See core.utils.
        schedule = PaymentSchedule.objects.create(
            engagement=engagement, total_investment=total_investment,
            created_by=request.user,
        )
        # A new schedule is born with the default 30/40/30 contract split — the
        # amounts are derived from total_investment (services.generate_milestones).
        services.generate_milestones(schedule)
        # ...and one invoice per milestone, already linked. Paying an invoice is
        # what moves this schedule, so issuing them here is what stops staff
        # having to enter the same three amounts twice.
        services.issue_invoices_for_schedule(schedule, issued_by=request.user)
    return Response(PaymentScheduleSerializer(schedule).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def update_payment_schedule(request: Request, schedule_id: str) -> Response:
    """
    Staff only.
    PATCH  — change total_investment (re-splits milestone amounts).
    DELETE — remove the schedule and every milestone under it (cascade). Refused
             unless ?confirm=true when milestones exist, and always refused when
             any milestone is already paid — that's a payment record, not a
             draft, and deleting it would silently erase what the client has
             been told they paid. Clear the paid milestones first if you really
             mean to. Mirrors delete_event's confirm-on-cascade contract.
    """
    schedule = PaymentSchedule.objects.filter(id=schedule_id).first()
    if schedule is None:
        return _error("Payment schedule not found.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        milestones = schedule.milestones.all()
        # PART_PAID counts as well: money that actually arrived is a payment
        # record whether or not it settled the milestone, and the old
        # PAID-only check would have let a schedule holding real part payments
        # be deleted without so much as a confirm.
        paid = milestones.exclude(status=PaymentMilestoneStatus.PENDING).count()
        total = milestones.count()
        if paid:
            return _error(
                f"This schedule has {paid} paid or part-paid milestone(s). Delete or unmark those "
                "first — a milestone with money against it is a payment record, not a draft.",
                VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST,
                errors={"impact": {"milestones": total, "paid_milestones": paid}},
            )
        if total and request.query_params.get("confirm", "").lower() != "true":
            return _error(
                "This schedule has milestones attached. Pass ?confirm=true to delete it and them.",
                CONFIRMATION_REQUIRED, status.HTTP_400_BAD_REQUEST,
                errors={"impact": {"milestones": total, "paid_milestones": 0}},
            )
        schedule.delete()
        return Response({"detail": "Payment schedule deleted."})

    if "total_investment" in request.data:
        total_investment = parse_decimal(request.data.get("total_investment"))
        if total_investment is None:
            return _error("total_investment must be a valid number.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            schedule.total_investment = total_investment
            schedule.save(update_fields=["total_investment", "updated_at"])
            # Percentage is the source of truth — re-split the milestones over
            # the new total so their amounts stay consistent with it.
            services.recompute_milestone_amounts(schedule)
    return Response(PaymentScheduleSerializer(schedule).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def add_milestone(request: Request, schedule_id: str) -> Response:
    """Staff only: add a milestone row (Deposit / Phase 2 / Final Payment, ...)."""
    schedule = PaymentSchedule.objects.filter(id=schedule_id).first()
    if schedule is None:
        return _error("Payment schedule not found.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    serializer = PaymentMilestoneSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid milestone data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    milestone = save_with_attribution(serializer, request.user, schedule=schedule)
    # An ad-hoc milestone is money the client owes, so it gets billed like every
    # other one. Skips milestones that already have an invoice, so this is the
    # same call the schedule-creation path makes.
    services.issue_invoices_for_schedule(schedule, issued_by=request.user)
    milestone.refresh_from_db()
    return Response(PaymentMilestoneSerializer(milestone).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def milestone_detail(request: Request, milestone_id: str) -> Response:
    milestone = PaymentMilestone.objects.filter(id=milestone_id).first()
    if milestone is None:
        return _error("Milestone not found.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        milestone.delete()
        return Response({"detail": "Milestone deleted."})

    serializer = PaymentMilestoneSerializer(milestone, data=request.data, partial=True)
    if not serializer.is_valid():
        return _error("Invalid milestone data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    milestone = save_with_attribution(serializer, request.user)
    # A due date agreed on the plan is the date the client should see on the
    # invoice for it — fills only invoices that have none of their own.
    services.propagate_due_date(milestone)
    return Response(PaymentMilestoneSerializer(milestone).data)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def mark_milestone_paid(request: Request, milestone_id: str) -> Response:
    """Staff only. Body (optional): {"paid_on": "2026-05-06"} — defaults to today."""
    milestone = PaymentMilestone.objects.filter(id=milestone_id).first()
    if milestone is None:
        return _error("Milestone not found.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    milestone = services.mark_milestone_paid(
        milestone, paid_on=request.data.get("paid_on"), updated_by=request.user,
    )
    return Response(PaymentMilestoneSerializer(milestone).data)


# ── Invoices ─────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def create_invoice(request: Request) -> Response:
    """Staff only. Body: portal_id + Invoice fields."""
    portal, err = _resolve_portal_by_id(request.data.get("portal_id"))
    if err:
        return err
    engagement, err = _resolve_active_engagement(portal, request.data.get("engagement_id"))
    if err:
        return err

    serializer = InvoiceSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid invoice data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    invoice = save_with_attribution(serializer, request.user, engagement=engagement)
    services.notify_invoice_issued(invoice)
    # An invoice can be created already paid (staff recording a payment after
    # the fact), so the sync runs on create too, not only on the status flip.
    services.sync_invoice_milestone(invoice)
    return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def invoice_detail(request: Request, invoice_id: str) -> Response:
    invoice = Invoice.objects.filter(id=invoice_id).first()
    if invoice is None:
        return _error("Invoice not found.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        # Held before the row goes, then re-synced after: deleting a PAID
        # invoice has to take its money back off the milestone, and the FK is
        # unreadable once the row is gone.
        milestone = invoice.milestone
        invoice.delete()
        if milestone is not None:
            services.sync_milestone_from_invoices(milestone)
        return Response({"detail": "Invoice deleted."})

    serializer = InvoiceSerializer(invoice, data=request.data, partial=True)
    if not serializer.is_valid():
        return _error("Invalid invoice data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    invoice = save_with_attribution(serializer, request.user)
    # THE fix for "invoices don't drive the payment schedule": flipping status
    # to paid here is what settles the linked milestone and moves the Payment
    # Overview tiles. Unlinked invoices no-op.
    services.sync_invoice_milestone(invoice)
    return Response(InvoiceSerializer(invoice).data)


# ── Receipts ─────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def create_receipt(request: Request) -> Response:
    """Staff only. Body: portal_id + Receipt fields."""
    portal, err = _resolve_portal_by_id(request.data.get("portal_id"))
    if err:
        return err
    engagement, err = _resolve_active_engagement(portal, request.data.get("engagement_id"))
    if err:
        return err

    serializer = ReceiptSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid receipt data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    receipt = save_with_attribution(serializer, request.user, engagement=engagement)
    services.notify_receipt_issued(receipt)
    return Response(ReceiptSerializer(receipt).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def receipt_detail(request: Request, receipt_id: str) -> Response:
    receipt = Receipt.objects.filter(id=receipt_id).first()
    if receipt is None:
        return _error("Receipt not found.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        receipt.delete()
        return Response({"detail": "Receipt deleted."})

    serializer = ReceiptSerializer(receipt, data=request.data, partial=True)
    if not serializer.is_valid():
        return _error("Invalid receipt data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user)
    return Response(serializer.data)
