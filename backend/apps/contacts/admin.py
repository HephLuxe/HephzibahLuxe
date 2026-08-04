from django.contrib import admin
from django.utils.html import format_html

from apps.core.admin import ATTRIBUTION_FIELDS, ATTRIBUTION_FIELDSET, AttributionAdminMixin

from .models import EventContact


@admin.register(EventContact)
class EventContactAdmin(AttributionAdminMixin, admin.ModelAdmin):
    list_display = [
        "name", "role", "category", "event", "event_day_display", "preferred_method",
        "photo_preview", "created_at", "created_by_display", "updated_at", "last_updated_by_display",
    ]
    list_filter = ["category", "preferred_method"]
    search_fields = ["name", "email", "phone", "event__title"]
    # Attribution FKs are editable=False — they belong in readonly_fields, never here.
    raw_id_fields = ["event", "event_day"]
    ordering = ["event", "category", "name"]
    readonly_fields = ["photo_preview", *ATTRIBUTION_FIELDS]

    @admin.display(description="Event Day")
    def event_day_display(self, obj):
        day_number = obj.event_day.day_number
        title = obj.event_day.event_day_title
        if day_number and title:
            return f"Day {day_number} — {title}"
        if day_number:
            return f"Day {day_number}"
        return title or "-"

    @admin.display(description="Photo")
    def photo_preview(self, obj):
        if obj.photo:
            return format_html('<img src="{}" style="max-height: 60px;" />', obj.photo.url)
        return "-"

    fieldsets = (
        (None, {
            "fields": ("event", "event_day", "category", "name", "role")
        }),
        ("Contact Info", {
            "fields": ("phone", "email", "preferred_method", "photo", "photo_preview")
        }),
        ATTRIBUTION_FIELDSET,
    )
