from django.contrib import admin

from apps.core.admin import ATTRIBUTION_FIELDS, ATTRIBUTION_FIELDSET, AttributionAdminMixin

from .models import Event, EventDay, EventImage
from .services import get_event_deletion_impact


class EventDayInline(admin.TabularInline):
    model = EventDay
    extra = 1
    fields = ('id', 'date', 'event_day_title', 'headline', 'start_time', 'end_time', 'venue', 'content')
    readonly_fields = ('id',)


class EventImageInline(admin.TabularInline):
    """
    Gallery rows, editable in place on the parent.

    `fk_name` is required because EventImage has TWO FKs that can point at the
    same object graph (`event` and `event_day`) — without it Django can't tell
    which one this inline hangs off and refuses to load the admin at all.
    """
    model = EventImage
    fk_name = 'event'
    extra = 1
    fields = ('id', 'image', 'event_day', 'alt_text', 'is_primary', 'sort_order')
    readonly_fields = ('id',)
    ordering = ('sort_order', 'created_at')


class EventDayImageInline(EventImageInline):
    fk_name = 'event_day'
    fields = ('id', 'image', 'alt_text', 'is_primary', 'sort_order')


@admin.register(Event)
class EventAdmin(AttributionAdminMixin, admin.ModelAdmin):
    # Fields to display in the admin list view
    list_display = (
        'title', 'headline', 'celebrant', 'event_date', 'event_type', 'country', 'state', 'slug',
        'created_at', 'created_by_display', 'updated_at', 'last_updated_by_display',
    )

    # Fields to filter by in the admin sidebar
    list_filter = ('event_type', 'country', 'event_date', 'created_at')

    # Fields to search by
    search_fields = ('title', 'headline', 'slug', 'celebrant__email', 'celebrant__first_name', 'celebrant__last_name')

    raw_id_fields = ('celebrant',)

    # Default ordering
    ordering = ('-created_at',)

    # Read-only fields
    readonly_fields = ('slug',) + ATTRIBUTION_FIELDS

    # Fields grouping in detail view
    fieldsets = (
        ('Event Information', {
            'fields': ('title', 'slug', 'celebrant', 'event_type', 'event_venue')
        }),
        ('Editorial', {
            'fields': ('headline', 'description'),
            'description': 'Public-page copy. `headline` is the editorial line; `title` above is generated from the celebrant names and drives the portal.',
        }),
        ('Naming Details', {
            'fields': ('groom_name', 'bride_name', 'honoree_name', 'event_name'),
            'description': 'Used by generate_event_title() when an event is created via the API — edit here for manual corrections.',
        }),
        ('Location', {
            'fields': ('country', 'state')
        }),
        ('Date', {
            'fields': ('event_date',)
        }),
        ATTRIBUTION_FIELDSET,
    )

    # Show EventDay inline (edit event days within event page)
    inlines = [EventDayInline, EventImageInline]

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
        'id', 'owner', 'event_day_title', 'headline', 'date', 'start_time', 'end_time', 'venue',
        'created_at', 'created_by_display', 'updated_at', 'last_updated_by_display',
    )

    # Fields to filter by in the admin sidebar
    list_filter = ('date', 'created_at', 'venue_booking_status')

    # Fields to search by
    search_fields = ('event_day_title', 'headline', 'owner__title', 'content', 'venue')

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
        ('Editorial', {
            'fields': ('headline', 'content'),
            'description': 'Public-page copy: `event_day_title` above is the eyebrow, `headline` the title, `content` the narrative paragraphs.',
        }),
        ('Timing', {
            'fields': ('start_time', 'end_time')
        }),
        ('Venue', {
            'fields': ('venue', 'venue_address', 'venue_booking_status', 'dress_code', 'estimated_guest_count')
        }),
        ATTRIBUTION_FIELDSET,
    )

    # Enable date hierarchy navigation
    date_hierarchy = 'date'

    inlines = [EventDayImageInline]


@admin.register(EventImage)
class EventImageAdmin(AttributionAdminMixin, admin.ModelAdmin):
    """
    Standalone gallery view, for finding an image without knowing which event or
    day it hangs off. Day-to-day editing happens in the inlines above.
    """
    list_display = (
        'id', 'event', 'event_day', 'is_primary', 'sort_order', 'alt_text',
        'created_at', 'created_by_display', 'updated_at', 'last_updated_by_display',
    )
    list_filter = ('is_primary', 'created_at')
    search_fields = ('alt_text', 'event__title', 'event_day__event_day_title')
    raw_id_fields = ('event', 'event_day')
    ordering = ('event', 'event_day', 'sort_order')
    readonly_fields = ('id',) + ATTRIBUTION_FIELDS

    fieldsets = (
        ('Image', {
            'fields': ('id', 'image', 'alt_text')
        }),
        ('Placement', {
            'fields': ('event', 'event_day', 'is_primary', 'sort_order'),
            'description': (
                'Leave `event_day` empty for an event-level image. `is_primary` marks the '
                'gallery cover — at most one per event and one per event day, enforced by a '
                'database constraint, so demote the current cover before promoting another '
                'here (the API endpoint does that swap for you).'
            ),
        }),
        ATTRIBUTION_FIELDSET,
    )
