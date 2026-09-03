# apps/budget/serializers.py

from decimal import Decimal

from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers

from apps.core.serializers import AttributionSerializerMixin, PrivateFileURLField
from apps.core.uploads import MAX_PHOTO_SIZE, validate_upload

from .models import BudgetCategory, BudgetHealthStatus, BudgetPayment, EventBudget

ALLOWED_RECEIPT_TYPES = ["application/pdf", "image/jpeg", "image/png", "image/webp"]


class BudgetCategorySerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    variance = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    percentage_of_spent = serializers.SerializerMethodField()

    class Meta:
        model = BudgetCategory
        fields = [
            "id", "category", "category_display",
            "estimated_amount", "actual_amount", "variance",
            "percentage_of_spent",
            "notes", "order", "updated_at",
            "created_by_display", "last_updated_by_display",
        ]
        read_only_fields = ["id", "updated_at"]

    def get_percentage_of_spent(self, obj: BudgetCategory) -> float:
        """This category's share of total spend — feeds the donut chart legend."""
        total_spent = obj.budget.spent
        if not total_spent:
            return 0.0
        return round(float(obj.actual_amount) / float(total_spent) * 100, 1)


class BudgetPaymentSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    # Write to `receipt`, read from `receipt_url` — see ClientDocumentSerializer.
    receipt_url = PrivateFileURLField("budget-receipt")

    class Meta:
        model = BudgetPayment
        fields = [
            "id", "payment_date", "vendor_item",
            "category", "category_display",
            "purpose", "amount",
            "status", "status_display",
            "receipt", "receipt_url", "created_at",
            "created_by_display", "last_updated_by_display",
        ]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"receipt": {"write_only": True}}

    def validate_receipt(self, value: UploadedFile | None) -> UploadedFile | None:
        # A receipt is a photo of a slip or a one-page PDF, so it is held to the
        # PHOTO ceiling rather than the document one — nothing legitimate here
        # approaches 5MB, and the fields that do are staff-only by design.
        return validate_upload(
            value,
            max_size=MAX_PHOTO_SIZE,
            allowed_types=tuple(ALLOWED_RECEIPT_TYPES),
            label="Receipt",
        )


class EventBudgetOverviewSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    """For the Overview tab — summary cards + categories + spending breakdown."""
    categories = BudgetCategorySerializer(many=True, read_only=True)
    allocated = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    spent = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    remaining = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    budget_health_percentage = serializers.FloatField(read_only=True)
    financial_status = serializers.CharField(read_only=True)
    financial_status_display = serializers.SerializerMethodField()

    class Meta:
        model = EventBudget
        fields = [
            "id", "total_budget",
            "allocated", "spent", "remaining",
            "budget_health_percentage",
            "financial_status", "financial_status_display",
            "categories", "updated_at",
            "created_by_display", "last_updated_by_display",
        ]
        read_only_fields = ["id", "updated_at"]

    def get_financial_status_display(self, obj: EventBudget) -> str:
        return BudgetHealthStatus(obj.financial_status).label


class EventBudgetPaymentSummarySerializer(serializers.ModelSerializer):
    """
    For the Payment History tab's summary cards only (stat tiles). The payment
    rows themselves are paginated separately in the view — see
    core.pagination.StandardPageNumberPagination — rather than nested here
    unbounded, so the endpoint matches the Figma's numbered-pagination table.
    """
    payments_made_total = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    payments_pending_total = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    payments_made_count = serializers.IntegerField(read_only=True)
    payments_pending_count = serializers.IntegerField(read_only=True)
    total_payments_amount = serializers.SerializerMethodField()
    total_payments_count = serializers.SerializerMethodField()

    class Meta:
        model = EventBudget
        fields = [
            "id",
            "payments_made_total", "payments_made_count",
            "payments_pending_total", "payments_pending_count",
            "total_payments_amount", "total_payments_count",
        ]

    def get_total_payments_amount(self, obj: EventBudget) -> Decimal:
        return obj.payments_made_total + obj.payments_pending_total

    def get_total_payments_count(self, obj: EventBudget) -> int:
        return obj.payments_made_count + obj.payments_pending_count
