from django.contrib import admin

from apps.core.admin import ATTRIBUTION_FIELDS, ATTRIBUTION_FIELDSET, AttributionAdminMixin
from django.utils.html import format_html
from rest_framework.exceptions import ValidationError

from . import services
from .models import ClientPortal, EventEngagement, PortalSettings, PortalTeamAssignment, TeamMember


@admin.register(PortalSettings)
class PortalSettingsAdmin(admin.ModelAdmin):
    """System-wide portal behaviour (phase auto-lock, notification debounce,
    "Contact Your Team" info). Singleton — one editable row; add/delete disabled."""
    fields = (
        "auto_lock_enabled", "auto_lock_phase", "auto_lock_event_details", "auto_lock_contacts",
        "event_details_notify_debounce_seconds",
        "contact_email", "contact_whatsapp",
    )

    def has_add_permission(self, request):
        return not PortalSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from django.shortcuts import redirect
        return redirect("admin:portal_portalsettings_change", PortalSettings.load().pk)


class PortalTeamAssignmentInline(admin.TabularInline):
    model = PortalTeamAssignment
    extra = 1
    autocomplete_fields = ["team_member"]


@admin.register(PortalTeamAssignment)
class PortalTeamAssignmentAdmin(AttributionAdminMixin, admin.ModelAdmin):
    """
    Standalone view of who is assigned to which portal. The usual way to manage
    these is the inline on ClientPortal (assign while looking at the client);
    this exists for the other direction — "which portals is Winnie on?" — which
    an inline can't answer.
    """
    list_display = ["team_member", "portal", "created_at", "created_by_display"]
    search_fields = ["team_member__name", "portal__user__email"]
    raw_id_fields = ["portal"]
    autocomplete_fields = ["team_member"]
    readonly_fields = [*ATTRIBUTION_FIELDS]


@admin.register(EventEngagement)
class EventEngagementAdmin(AttributionAdminMixin, admin.ModelAdmin):
    list_display = ["portal", "event", "is_active", "current_phase", "contacts_locked", "event_details_locked", "created_by_display", "last_updated_by_display"]
    list_filter = ["is_active", "current_phase", "contacts_locked", "event_details_locked"]
    search_fields = ["portal__user__email", "event__title"]
    raw_id_fields = ["portal", "event"]
    readonly_fields = [*ATTRIBUTION_FIELDS]
    actions = ["make_active_engagement", "advance_to_next_phase"]

    @admin.action(description="Make this the portal's active engagement")
    def make_active_engagement(self, request, queryset):
        for engagement in queryset:
            services.activate_engagement(engagement.portal, engagement.event)
        self.message_user(request, f"Activated {queryset.count()} engagement(s) (deactivating any other active engagement per portal).")

    @admin.action(description="Advance to next planning phase")
    def advance_to_next_phase(self, request, queryset):
        advanced, errors = 0, []
        for engagement in queryset:
            try:
                services.advance_phase(engagement.portal)
                advanced += 1
            except ValidationError as e:
                errors.append(f"{engagement}: {e.detail[0]}")
        if advanced:
            self.message_user(request, f"Advanced {advanced} engagement(s) to their next phase.")
        for err in errors:
            self.message_user(request, err, level="error")


@admin.register(ClientPortal)
class ClientPortalAdmin(AttributionAdminMixin, admin.ModelAdmin):
    list_display = ["user", "current_phase_display", "created_at", "updated_at", "created_by_display", "last_updated_by_display"]
    search_fields = ["user__email", "user__username"]
    raw_id_fields = ["user"]
    readonly_fields = [*ATTRIBUTION_FIELDS]
    inlines = [PortalTeamAssignmentInline]
    fieldsets = (
        (None, {"fields": ("user", "welcome_message")}),
        ATTRIBUTION_FIELDSET,
    )

    @admin.display(description="Current Phase")
    def current_phase_display(self, obj):
        return obj.current_phase or "-"


@admin.register(TeamMember)
class TeamMemberAdmin(AttributionAdminMixin, admin.ModelAdmin):
    list_display = ["name", "role", "email", "phone", "is_default", "photo_preview", "created_by_display", "last_updated_by_display"]
    list_filter = ["is_default"]
    list_editable = ["is_default"]
    search_fields = ["name", "role", "email"]
    ordering = ["name"]
    fieldsets = (
        (None, {"fields": ("name", "role", "bio", "is_default")}),
        ("Contact", {"fields": ("email", "phone")}),
        ("Photo", {"fields": ("photo", "photo_preview")}),
        ATTRIBUTION_FIELDSET,
    )
    readonly_fields = ["photo_preview", *ATTRIBUTION_FIELDS]

    @admin.display(description="Photo")
    def photo_preview(self, obj):
        if obj.photo:
            return format_html('<img src="{}" style="max-height: 60px;" />', obj.photo.url)
        return "-"
