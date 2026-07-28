"""
apps/core/admin.py

Shared admin building blocks.

AttributionAdminMixin — the structured "who did what, when" block that every
attributed model's admin shows, so attribution reads the same everywhere instead
of each app inventing its own columns.
"""

from django.contrib import admin

from .utils import user_display_name

# The four audit fields, in who/when order. Always readonly: they're
# editable=False on the model (see core.models.AttributedModel), so listing them
# in raw_id_fields or an editable fieldset raises an admin check error.
ATTRIBUTION_FIELDS = ("created_by", "created_at", "last_updated_by", "updated_at")

# Drop-in detail-view section. Spread into a ModelAdmin's fieldsets:
#     fieldsets = ( ..., ATTRIBUTION_FIELDSET )
ATTRIBUTION_FIELDSET = (
    "Attribution",
    {
        "fields": ATTRIBUTION_FIELDS,
        "classes": ("collapse",),
        "description": "System-recorded: who created this record and who last changed it.",
    },
)


class AttributionAdminMixin:
    """
    Mix into a ModelAdmin to get "Created by" / "Updated by" name columns.

    Use the *_display methods in list_display (the raw FKs would render as the
    user's email via User.__str__); add ATTRIBUTION_FIELDS to readonly_fields and
    ATTRIBUTION_FIELDSET to fieldsets for the detail view.
    """

    @admin.display(description="Created by")
    def created_by_display(self, obj):
        return user_display_name(getattr(obj, "created_by", None)) or "—"

    @admin.display(description="Updated by")
    def last_updated_by_display(self, obj):
        return user_display_name(getattr(obj, "last_updated_by", None)) or "—"
