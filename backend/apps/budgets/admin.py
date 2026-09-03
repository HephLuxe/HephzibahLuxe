from django.contrib import admin

from apps.core.admin import ATTRIBUTION_FIELDS, ATTRIBUTION_FIELDSET, AttributionAdminMixin
from apps.core.admin_files import PrivateFileAdminMixin

from .models import BudgetCategory, BudgetPayment, EventBudget, PaymentStatus


class BudgetCategoryInline(admin.TabularInline):
    model = BudgetCategory
    extra = 0
    fields = ["category", "estimated_amount", "actual_amount", "notes", "order"]


class BudgetPaymentInline(admin.TabularInline):
    model = BudgetPayment
    extra = 0
    fields = ["payment_date", "vendor_item", "category", "purpose", "amount", "status"]
    show_change_link = True


@admin.register(EventBudget)
class EventBudgetAdmin(AttributionAdminMixin, admin.ModelAdmin):
    list_display = ["event", "total_budget", "allocated", "spent", "remaining", "financial_status", "created_by_display", "last_updated_by_display"]
    search_fields = ["event__title"]
    raw_id_fields = ["event"]
    readonly_fields = [
        "allocated", "spent", "remaining", "budget_health_percentage", "financial_status",
        "payments_made_total", "payments_pending_total", "payments_made_count", "payments_pending_count",
        *ATTRIBUTION_FIELDS,
    ]
    inlines = [BudgetCategoryInline, BudgetPaymentInline]
    fieldsets = (
        (None, {"fields": ("event", "total_budget")}),
        ("Computed", {"fields": (
            "allocated", "spent", "remaining", "budget_health_percentage", "financial_status",
            "payments_made_total", "payments_made_count", "payments_pending_total", "payments_pending_count",
        )}),
        ATTRIBUTION_FIELDSET,
    )


@admin.register(BudgetCategory)
class BudgetCategoryAdmin(AttributionAdminMixin, admin.ModelAdmin):
    list_display = ["category", "budget", "estimated_amount", "actual_amount", "variance", "order", "created_by_display", "last_updated_by_display"]
    list_filter = ["category"]
    search_fields = ["budget__event__title", "notes"]
    raw_id_fields = ["budget"]
    readonly_fields = ["variance", *ATTRIBUTION_FIELDS]
    ordering = ["budget", "order", "category"]


@admin.register(BudgetPayment)
class BudgetPaymentAdmin(PrivateFileAdminMixin, AttributionAdminMixin, admin.ModelAdmin):
    private_file_type = "budget-receipt"
    readonly_fields = ["file_link", *ATTRIBUTION_FIELDS]
    list_display = ["vendor_item", "budget", "category", "purpose", "amount", "status", "payment_date", "file_link", "created_by_display", "last_updated_by_display"]
    list_filter = ["status", "category"]
    search_fields = ["vendor_item", "purpose", "budget__event__title"]
    raw_id_fields = ["budget"]
    ordering = ["-payment_date"]
    actions = ["mark_as_paid", "mark_as_pending"]

    @admin.action(description="Mark selected payments as paid")
    def mark_as_paid(self, request, queryset):
        updated = queryset.update(status=PaymentStatus.PAID)
        self.message_user(request, f"Marked {updated} payment(s) as paid.")

    @admin.action(description="Mark selected payments as pending")
    def mark_as_pending(self, request, queryset):
        updated = queryset.update(status=PaymentStatus.PENDING)
        self.message_user(request, f"Marked {updated} payment(s) as pending.")
