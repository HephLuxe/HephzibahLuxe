from django.contrib import admin

from apps.core.admin import ATTRIBUTION_FIELDS, AttributionAdminMixin
from rest_framework.exceptions import ValidationError

from . import services
from .models import (
    Meeting,
    MeetingNotes,
    MeetingPrepItem,
    MeetingStatus,
    PrepItemField,
    PrepItemFileUpload,
    PrepItemResponse,
)


class PrepItemFileUploadInline(admin.TabularInline):
    model = PrepItemFileUpload
    extra = 0
    readonly_fields = ["uploaded_at"]


class PrepItemFieldInline(admin.TabularInline):
    model = PrepItemField
    extra = 1
    fields = ["field_type", "label", "helper_text", "is_required", "order"]


class MeetingPrepItemInline(admin.TabularInline):
    model = MeetingPrepItem
    extra = 1
    fields = ["title", "description", "is_completed", "order"]
    show_change_link = True


class MeetingNotesInline(admin.StackedInline):
    model = MeetingNotes
    extra = 0
    fields = ["summary", "key_discussions", "key_decisions", "action_items"]


def _make_transition_action(target_status, description):
    @admin.action(description=description)
    def action(self, request, queryset):
        moved, errors = 0, []
        for meeting in queryset:
            try:
                services.transition_meeting_status(meeting, target_status)
                moved += 1
            except ValidationError as e:
                errors.append(f"{meeting}: {e.detail[0]}")
        if moved:
            self.message_user(request, f"{moved} meeting(s) moved to '{target_status}'.")
        for err in errors:
            self.message_user(request, err, level="error")
    action.__name__ = f"mark_{target_status}"
    return action


@admin.register(Meeting)
class MeetingAdmin(AttributionAdminMixin, admin.ModelAdmin):
    readonly_fields = [*ATTRIBUTION_FIELDS]
    list_display = ["title", "get_portal", "date", "time", "status", "phase", "preparation_required", "created_by_display", "last_updated_by_display"]
    list_filter = ["status", "phase"]
    search_fields = ["title", "engagement__portal__user__email", "engagement__portal__user__first_name"]
    raw_id_fields = ["engagement"]
    ordering = ["date", "time"]
    inlines = [MeetingPrepItemInline, MeetingNotesInline]
    actions = [
        _make_transition_action(MeetingStatus.ACTIVE, "Mark as Active"),
        _make_transition_action(MeetingStatus.COMPLETED, "Mark as Completed"),
        _make_transition_action(MeetingStatus.CANCELLED, "Mark as Cancelled"),
        _make_transition_action(MeetingStatus.RESCHEDULED, "Mark as Rescheduled"),
        _make_transition_action(MeetingStatus.UPCOMING, "Mark as Upcoming"),
    ]

    @admin.display(description="Portal")
    def get_portal(self, obj):
        return obj.engagement.portal if obj.engagement else "-"


@admin.register(MeetingPrepItem)
class MeetingPrepItemAdmin(admin.ModelAdmin):
    list_display = ["title", "meeting", "is_completed", "order"]
    list_filter = ["is_completed"]
    search_fields = ["title", "meeting__title"]
    raw_id_fields = ["meeting"]
    ordering = ["meeting", "order"]
    inlines = [PrepItemFieldInline]
    actions = ["resync_completion"]

    @admin.action(description="Re-sync completion from field responses")
    def resync_completion(self, request, queryset):
        # Completion is always derived from field state now (there is no manual
        # "mark complete" — that path was removed because it could set a
        # completion the next sync would silently revert). This just recomputes
        # is_completed for the selected items.
        for item in queryset:
            services.sync_prep_item_completion(item)
        self.message_user(request, f"Re-synced completion state for {queryset.count()} prep item(s).")


@admin.register(PrepItemField)
class PrepItemFieldAdmin(admin.ModelAdmin):
    list_display = ["label", "field_type", "prep_item", "is_required", "order"]
    list_filter = ["field_type", "is_required"]
    search_fields = ["label", "prep_item__title"]
    raw_id_fields = ["prep_item"]
    inlines = [PrepItemFileUploadInline]


@admin.register(PrepItemResponse)
class PrepItemResponseAdmin(admin.ModelAdmin):
    list_display = ["field", "submitted_at"]
    search_fields = ["field__label", "field__prep_item__title"]
    raw_id_fields = ["field"]
    readonly_fields = ["submitted_at"]


@admin.register(PrepItemFileUpload)
class PrepItemFileUploadAdmin(admin.ModelAdmin):
    list_display = ["field", "filename", "uploaded_at"]
    search_fields = ["filename", "field__label"]
    raw_id_fields = ["field"]
    readonly_fields = ["uploaded_at"]


@admin.register(MeetingNotes)
class MeetingNotesAdmin(AttributionAdminMixin, admin.ModelAdmin):
    list_display = ["meeting", "get_portal", "get_client_email", "created_at", "created_by_display", "last_updated_by_display"]
    search_fields = ["meeting__title", "meeting__engagement__portal__user__email", "summary"]
    raw_id_fields = ["meeting"]
    readonly_fields = [*ATTRIBUTION_FIELDS]

    @admin.display(description="Portal")
    def get_portal(self, obj):
        return obj.meeting.engagement.portal if obj.meeting.engagement else "-"

    @admin.display(description="Client")
    def get_client_email(self, obj):
        return obj.meeting.engagement.portal.user.email if obj.meeting.engagement else "-"
