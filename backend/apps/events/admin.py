from django.contrib import admin

from apps.core.admin import ATTRIBUTION_FIELDS, ATTRIBUTION_FIELDSET, AttributionAdminMixin

from .models import Event, EventDay
from .services import get_event_deletion_impact


class EventDayInline(admin.TabularInline):
    model = EventDay
    extra = 1
    fields = ('id', 'date', 'event_day_title', 'start_time', 'end_time', 'venue', 'content', 'event_images')
    readonly_fields = ('id',)


@admin.register(Event)
class EventAdmin(AttributionAdminMixin, admin.ModelAdmin):
    # Fields to display in the admin list view
    list_display = (
        'title', 'celebrant', 'event_date', 'event_type', 'country', 'state', 'slug',
        'created_at', 'created_by_display', 'updated_at', 'last_updated_by_display',
    )

    # Fields to filter by in the admin sidebar
    list_filter = ('event_type', 'country', 'event_date', 'created_at')

    # Fields to search by
    search_fields = ('title', 'slug', 'celebrant__email', 'celebrant__first_name', 'celebrant__last_name')

    raw_id_fields = ('celebrant',)

    # Default ordering
    ordering = ('-created_at',)

    # Read-only fields
    readonly_fields = ('slug',) + ATTRIBUTION_FIELDS

    # Fields grouping in detail view
    fieldsets = (
        ('Event Information', {
            'fields': ('title', 'slug', 'celebrant', 'event_type', 'event_venue', 'description')
        }),
        ('Naming Details', {
            'fields': ('groom_name', 'bride_name', 'honoree_name', 'event_name'),
            'description': 'Used by generate_event_title() when an event is created via the API — edit here for manual corrections.',
        }),
        ('Location', {
            'fields': ('country', 'state')
        }),
        ('Date & Media', {
            'fields': ('event_date', 'featured_image')
        }),
        ATTRIBUTION_FIELDSET,
    )

    # Show EventDay inline (edit event days within event page)
    inlines = [EventDayInline]

    # Enable date hierarchy navigation
    date_hierarchy = 'event_date'

    actions = ['show_deletion_impact']

    @admin.action(description="Show what would cascade-delete (dry run)")
    def show_deletion_impact(self, request, queryset):
        for event in queryset:
            impact = get_event_deletion_impact(event)
            total = impact.pop("total")
            if total == 0:
                self.message_user(request, f"'{event.title}': nothing else attached — safe to delete.")
                continue
            breakdown = ", ".join(f"{k}: {v}" for k, v in impact.items() if v)
            self.message_user(request, f"'{event.title}': deleting cascades to {total} related record(s) — {breakdown}", level="warning")


@admin.register(EventDay)
class EventDayAdmin(AttributionAdminMixin, admin.ModelAdmin):
    # Fields to display in the admin list view
    list_display = (
        'id', 'owner', 'event_day_title', 'date', 'start_time', 'end_time', 'venue',
        'created_at', 'created_by_display', 'updated_at', 'last_updated_by_display',
    )

    # Fields to filter by in the admin sidebar
    list_filter = ('date', 'created_at', 'venue_booking_status')

    # Fields to search by
    search_fields = ('event_day_title', 'owner__title', 'content', 'venue')

    raw_id_fields = ('owner',)

    # Default ordering
    ordering = ('date', 'start_time')

    # Read-only fields
    readonly_fields = ('id',) + ATTRIBUTION_FIELDS

    # Fields grouping in detail view
    fieldsets = (
        ('Event Day Information', {
            'fields': ('id', 'owner', 'event_day_title', 'date')
        }),
        ('Timing', {
            'fields': ('start_time', 'end_time')
        }),
        ('Venue', {
            'fields': ('venue', 'venue_address', 'venue_booking_status', 'dress_code', 'estimated_guest_count')
        }),
        ('Content & Media', {
            'fields': ('content', 'event_images')
        }),
        ATTRIBUTION_FIELDSET,
    )

    # Enable date hierarchy navigation
    date_hierarchy = 'date'
