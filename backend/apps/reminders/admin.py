from django.contrib import admin

from apps.core.admin import ATTRIBUTION_FIELDS, ATTRIBUTION_FIELDSET, AttributionAdminMixin

from . import services
from .models import Reminder


@admin.register(Reminder)
class ReminderAdmin(AttributionAdminMixin, admin.ModelAdmin):
    list_display = (
        "title", "priority", "due_date", "is_completed", "engagement", "deep_link",
        "created_at", "created_by_display", "updated_at", "last_updated_by_display",
    )
    list_filter = ("priority", "is_completed", "due_date")
    search_fields = ("title", "description")
    # Attribution FKs are editable=False — they belong in readonly_fields, never here.
    raw_id_fields = ("engagement", "target_content_type")
    readonly_fields = ("completed_at", "deep_link") + ATTRIBUTION_FIELDS
    ordering = ("is_completed", "order", "due_date")
    actions = ["mark_completed", "mark_pending"]
    fieldsets = (
        (None, {"fields": ("engagement", "title", "description", "priority", "due_date", "order")}),
        ("Deep link", {
            "fields": ("target_content_type", "target_object_id", "link_url", "link_label", "deep_link"),
            "description": (
                "Point the reminder at an object (content type + id) and the URL is derived from "
                "apps/core/deeplinks.py. Use link_url only for links with no object behind them. "
                "Note: unlike the API, the admin does not check the target belongs to this "
                "engagement — set it carefully."
            ),
        }),
        ("Status", {"fields": ("is_completed", "completed_at")}),
        ATTRIBUTION_FIELDSET,
    )

    @admin.display(description="Resolved link")
    def deep_link(self, obj):
        url = obj.resolved_link_url
        if not url:
            return "-"
        return f"{obj.resolved_link_label or 'Open'} → {url}"

    @admin.action(description="Mark selected reminders as completed")
    def mark_completed(self, request, queryset):
        for reminder in queryset:
            services.set_completed(reminder, True, updated_by=request.user)
        self.message_user(request, f"Marked {queryset.count()} reminder(s) as completed.")

    @admin.action(description="Mark selected reminders as pending")
    def mark_pending(self, request, queryset):
        for reminder in queryset:
            services.set_completed(reminder, False, updated_by=request.user)
        self.message_user(request, f"Marked {queryset.count()} reminder(s) as pending.")
