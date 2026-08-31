# apps/budgets/views.py

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.error_codes import CONFIRMATION_REQUIRED, VALIDATION_ERROR
from apps.core.pagination import StandardPageNumberPagination
from apps.core.permissions import IsStaffOrSuperuser, can_access_event, enforce
from apps.core.utils import parse_decimal, save_with_attribution, stamp_attribution
from apps.events.models import Event

from .models import BudgetCategory, BudgetPayment, EventBudget
from .serializers import (
    BudgetCategorySerializer,
    BudgetPaymentSerializer,
    EventBudgetOverviewSerializer,
    EventBudgetPaymentSummarySerializer,
)

# ── Envelope helper (see apps/core/exceptions.py for the exception-path half) ──

def _error(detail: str, code: str, http_status: int, errors: dict | None = None) -> Response:
    body: dict = {"detail": detail, "code": code}
    if errors:
        body["errors"] = errors
    return Response(body, status=http_status)


# ── Budget Overview (Overview tab) ────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def budget_overview(request, event_slug):
    """GET — Overview tab: summary cards, financial status, spending breakdown, category table."""
    event = get_object_or_404(Event, slug=event_slug)
    enforce(can_access_event(request.user, event))
    budget = get_object_or_404(EventBudget, event=event)
    serializer = EventBudgetOverviewSerializer(budget)
    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def budget_create(request, event_slug):
    """POST — Staff creates the budget record for an event."""
    event = get_object_or_404(Event, slug=event_slug)
    if EventBudget.objects.filter(event=event).exists():
        return _error("A budget already exists for this event.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    total_budget = parse_decimal(request.data.get("total_budget", 0))
    if total_budget is None:
        return _error("total_budget must be a valid number.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    # created_by only — creating is not editing. See core.utils.
    budget = EventBudget.objects.create(
        event=event, total_budget=total_budget,
        created_by=request.user,
    )
    serializer = EventBudgetOverviewSerializer(budget)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def budget_update(request, event_slug):
    """
    Staff only.
    PATCH  — update the total budget amount.
    DELETE — remove the budget and everything under it (categories + payments)
             via cascade. Refused unless ?confirm=true when anything is attached;
             the error body carries the same impact breakdown so the caller can
             show it first. Mirrors delete_event's confirm-on-cascade contract.

             Note: a payment's uploaded receipt blob is not removed by the
             cascade (Django never deletes FileField blobs) — run
             `manage.py cleanup_orphaned_documents` to sweep those.
    """
    event = get_object_or_404(Event, slug=event_slug)
    budget = get_object_or_404(EventBudget, event=event)

    if request.method == "DELETE":
        impact = {
            "categories": budget.categories.count(),
            "payments": budget.payments.count(),
        }
        impact["total"] = impact["categories"] + impact["payments"]
        if impact["total"] and request.query_params.get("confirm", "").lower() != "true":
            return _error(
                "This budget has categories/payments attached. Review the impact and pass "
                "?confirm=true to delete it and them.",
                CONFIRMATION_REQUIRED, status.HTTP_400_BAD_REQUEST, errors={"impact": impact},
            )
        budget.delete()
        return Response({"detail": "Budget deleted."})

    if "total_budget" in request.data:
        total_budget = parse_decimal(request.data.get("total_budget"))
        if total_budget is None:
            return _error("total_budget must be a valid number.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)
        budget.total_budget = total_budget
        stamp_attribution(budget, request.user, creating=False)
        budget.save()

    serializer = EventBudgetOverviewSerializer(budget)
    return Response(serializer.data)


# ── Budget Categories ─────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def category_create(request, event_slug):
    """POST — Staff adds a category row to the budget."""
    event = get_object_or_404(Event, slug=event_slug)
    budget = get_object_or_404(EventBudget, event=event)
    serializer = BudgetCategorySerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid category data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user, budget=budget)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def category_detail(request, event_slug, category_id):
    """PATCH/DELETE — Staff updates or removes a category row."""
    event = get_object_or_404(Event, slug=event_slug)
    budget = get_object_or_404(EventBudget, event=event)
    category = get_object_or_404(BudgetCategory, id=category_id, budget=budget)

    if request.method == "DELETE":
        category.delete()
        return Response({"detail": "Category deleted."})

    serializer = BudgetCategorySerializer(category, data=request.data, partial=True)
    if not serializer.is_valid():
        return _error("Invalid category data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user)
    return Response(serializer.data)


# ── Payment History (Payment History tab) ─────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def payment_history(request, event_slug):
    """
    GET — Payment History tab: summary cards + paginated, filterable payment list.

    Query params:
      ?status=paid|pending      filter by payment status
      ?category=<category>     filter by budget category
      ?vendor_item=<text>      case-insensitive substring match on vendor/item name
      ?sort=oldest              oldest-first (default: newest-first, matches Figma "Sort By")
      ?page=&?page_size=        pagination (default page_size=7, matches Figma "1 to 7 of 14")
    """
    event = get_object_or_404(Event, slug=event_slug)
    enforce(can_access_event(request.user, event))
    budget = get_object_or_404(EventBudget, event=event)

    payments = budget.payments.all()

    status_filter = request.query_params.get("status")
    if status_filter:
        payments = payments.filter(status=status_filter)

    category_filter = request.query_params.get("category")
    if category_filter:
        payments = payments.filter(category=category_filter)

    vendor_filter = request.query_params.get("vendor_item")
    if vendor_filter:
        payments = payments.filter(vendor_item__icontains=vendor_filter)

    sort = request.query_params.get("sort")
    payments = payments.order_by("payment_date") if sort == "oldest" else payments.order_by("-payment_date")

    paginator = StandardPageNumberPagination()
    page = paginator.paginate_queryset(payments, request)
    paginated_payments = paginator.get_paginated_response(
        BudgetPaymentSerializer(page, many=True).data
    ).data

    summary = EventBudgetPaymentSummarySerializer(budget).data
    summary["payments"] = paginated_payments
    return Response(summary)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def payment_create(request, event_slug):
    """POST — Staff logs a payment."""
    event = get_object_or_404(Event, slug=event_slug)
    budget = get_object_or_404(EventBudget, event=event)
    serializer = BudgetPaymentSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid payment data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user, budget=budget)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def payment_detail(request, event_slug, payment_id):
    """PATCH/DELETE — Staff updates or removes a payment."""
    event = get_object_or_404(Event, slug=event_slug)
    budget = get_object_or_404(EventBudget, event=event)
    payment = get_object_or_404(BudgetPayment, id=payment_id, budget=budget)

    if request.method == "DELETE":
        payment.delete()
        return Response({"detail": "Payment deleted."})

    serializer = BudgetPaymentSerializer(payment, data=request.data, partial=True)
    if not serializer.is_valid():
        return _error("Invalid payment data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user)
    return Response(serializer.data)
