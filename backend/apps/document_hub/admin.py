from decimal import Decimal

from django import forms
from django.contrib import admin

from apps.core.admin import ATTRIBUTION_FIELDS, AttributionAdminMixin
from apps.core.admin_files import PrivateFileAdminMixin

from . import services
from .models import (
    ClientDocument,
    Invoice,
    PaymentMilestone,
    PaymentSchedule,
    PortalDefaults,
    Receipt,
    ReferenceCounter,
)


@admin.register(ReferenceCounter)
class ReferenceCounterAdmin(admin.ModelAdmin):
    """
    The counters behind auto-generated reference codes — visibility only.

    Scopes you'll see: `eventtype:<LETTER>` (the global per-event-type segment
    counter, e.g. `eventtype:W` = how many weddings have been numbered) and
    `<engagement_pk>:<TYPE>` (per-engagement suffixes: C/Q/INV/R).

    Fully read-only on purpose: editing a counter silently changes what the NEXT
    code will be, and lowering one produces duplicates that collide with codes
    already issued to clients. Add-permission is off for the same reason —
    counters are created on demand by document_hub.services.
    """
    list_display = ("scope", "value")
    search_fields = ("scope",)
    ordering = ("scope",)
    readonly_fields = ("scope", "value")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ClientDocument)
class ClientDocumentAdmin(PrivateFileAdminMixin, AttributionAdminMixin, admin.ModelAdmin):
    # `file_link` goes through /admin-files/, which signs at click time — the raw
    # `file.url` would embed a signature that is dead before anyone reads the
    # page. See apps/core/admin_files.py.
    private_file_type = "client-document"
    list_display = ("title", "category", "reference_code", "file_link", "is_signed", "signed_on", "engagement", "created_by_display", "last_updated_by_display")
    list_filter = ("category", "is_signed")
    search_fields = ("title", "reference_code")
    raw_id_fields = ("engagement",)
    # reference_code is system-generated (pre_save signal) — never typed.
    readonly_fields = ("reference_code", "file_link", *ATTRIBUTION_FIELDS)
    actions = ["mark_as_signed"]

    @admin.action(description="Mark selected documents as signed today")
    def mark_as_signed(self, request, queryset):
        from django.utils import timezone
        updated = queryset.update(is_signed=True, signed_on=timezone.now().date())
        self.message_user(request, f"Marked {updated} document(s) as signed.")


@admin.register(PortalDefaults)
class PortalDefaultsAdmin(admin.ModelAdmin):
    """
    The single global defaults record — the Service Agreement / Welcome Booklet
    / FAQ templates + default welcome message seeded onto every new client.
    Registered as a singleton: staff edit the one row; adding a second or
    deleting are both disabled.
    """
    fields = ("service_agreement_file", "welcome_booklet_file", "faq_file", "welcome_message")

    def has_add_permission(self, request):
        # Only allow adding when none exists yet (singleton).
        return not PortalDefaults.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # Jump straight to the single row's change page instead of a 1-item list.
        from django.shortcuts import redirect
        defaults = PortalDefaults.load()
        return redirect("admin:document_hub_portaldefaults_change", defaults.pk)


class PaymentMilestoneInlineFormSet(forms.BaseInlineFormSet):
    """
    Enforce the sum-to-100 rule when staff edit a schedule's split. Only rows
    that carry a percentage (the structured split) are checked; ad-hoc,
    fixed-amount milestones (percentage left blank) sit outside the rule. Rows
    marked for deletion are ignored.
    """

    def clean(self):
        super().clean()
        if any(self.errors):
            return  # per-row errors already raised; don't pile on

        total = Decimal("0")
        has_percentage_rows = False
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            pct = form.cleaned_data.get("percentage")
            if pct is not None:
                has_percentage_rows = True
                total += pct

        if has_percentage_rows and total != Decimal("100"):
            raise forms.ValidationError(
                f"Percentage-based milestones must sum to 100 (got {total}). "
                "Leave percentage blank only for one-off, fixed-amount milestones."
            )


class PaymentMilestoneInline(admin.TabularInline):
    model = PaymentMilestone
    formset = PaymentMilestoneInlineFormSet
    extra = 0
    fields = ("label", "percentage", "amount", "amount_paid", "due_date", "status", "paid_on", "order")
    # amount is derived from percentage x total_investment (recomputed in
    # save_related), so staff set the percentage, not the amount, here.
    #
    # amount_paid/status/paid_on are derived from the milestone's INVOICES
    # (services.sync_milestone_from_invoices) — editing them here would be
    # overwritten by the next invoice change and would put the two records into
    # silent disagreement. Mark the invoice paid instead; for a milestone with
    # no invoice, use the "Mark selected milestones as paid" action.
    readonly_fields = ("amount", "amount_paid", "status", "paid_on")


@admin.register(PaymentSchedule)
class PaymentScheduleAdmin(admin.ModelAdmin):
    list_display = ("engagement", "total_investment", "paid_to_date", "remaining_balance", "next_payment_milestone")
    raw_id_fields = ("engagement",)
    readonly_fields = ("paid_to_date", "remaining_balance", "next_payment_milestone")
    inlines = [PaymentMilestoneInline]
    actions = ["reset_to_default_split"]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        schedule = form.instance
        # A schedule saved with no milestones (e.g. created straight from the
        # admin) is seeded with the default 30/40/30 split; otherwise the
        # percentage/total edits just made are pushed into the derived amounts.
        if not schedule.milestones.exists():
            services.generate_milestones(schedule)
        else:
            services.recompute_milestone_amounts(schedule)

    @admin.action(description="Reset to default 30/40/30 split")
    def reset_to_default_split(self, request, queryset):
        for schedule in queryset:
            services.generate_milestones(schedule)
        self.message_user(request, f"Reset {queryset.count()} schedule(s) to the default 30/40/30 split.")


@admin.register(PaymentMilestone)
class PaymentMilestoneAdmin(admin.ModelAdmin):
    list_display = ("label", "schedule", "amount", "amount_paid", "balance", "due_date", "status", "paid_on", "order")
    readonly_fields = ("amount_paid", "status", "paid_on")
    list_filter = ("status",)
    search_fields = ("label", "schedule__engagement__portal__user__email")
    raw_id_fields = ("schedule",)
    ordering = ("schedule", "order", "due_date")
    actions = ["mark_as_paid"]

    @admin.action(description="Mark selected milestones as paid")
    def mark_as_paid(self, request, queryset):
        for milestone in queryset:
            services.mark_milestone_paid(milestone)
        self.message_user(request, f"Marked {queryset.count()} milestone(s) as paid.")


@admin.register(Invoice)
class InvoiceAdmin(PrivateFileAdminMixin, AttributionAdminMixin, admin.ModelAdmin):
    private_file_type = "invoice"
    list_display = ("invoice_number", "engagement", "milestone", "issued_on", "due_on", "amount", "status", "file_link", "created_by_display", "last_updated_by_display")
    list_filter = ("status",)
    search_fields = ("invoice_number",)
    raw_id_fields = ("engagement", "milestone")
    # invoice_number is system-generated (pre_save signal) — filled on save.
    readonly_fields = ("invoice_number", "file_link", *ATTRIBUTION_FIELDS)
    actions = ["mark_as_paid"]

    def save_model(self, request, obj, form, change):
        """Push the saved state onto the linked milestone, same as the API does."""
        super().save_model(request, obj, form, change)
        services.sync_invoice_milestone(obj)

    def delete_model(self, request, obj):
        milestone = obj.milestone
        super().delete_model(request, obj)
        if milestone is not None:
            services.sync_milestone_from_invoices(milestone)

    @admin.action(description="Mark selected invoices as paid")
    def mark_as_paid(self, request, queryset):
        """Row-by-row, NOT queryset.update().

        A bulk UPDATE writes the status column and returns — it fires no signal
        and calls no service, so the linked milestones would stay pending and
        the Payment Overview would not move. That is exactly the class of bug
        this whole feature exists to fix, so it must not be reintroduced by the
        admin.
        """
        from .models import InvoiceStatus

        updated = 0
        for invoice in queryset:
            if invoice.status != InvoiceStatus.PAID:
                invoice.status = InvoiceStatus.PAID
                invoice.save(update_fields=["status", "updated_at"])
            services.sync_invoice_milestone(invoice)
            updated += 1
        self.message_user(request, f"Marked {updated} invoice(s) as paid.")


@admin.register(Receipt)
class ReceiptAdmin(PrivateFileAdminMixin, AttributionAdminMixin, admin.ModelAdmin):
    private_file_type = "receipt"
    list_display = ("receipt_number", "engagement", "paid_on", "payment_for", "amount", "file_link", "created_by_display", "last_updated_by_display")
    search_fields = ("receipt_number", "payment_for")
    raw_id_fields = ("engagement",)
    # receipt_number is system-generated (pre_save signal) — filled on save.
    readonly_fields = ("receipt_number", "file_link", *ATTRIBUTION_FIELDS)
