import csv
from datetime import datetime

from django.contrib import admin
from django.http import HttpResponse
from django.utils import timezone

from apps.core.admin import (
    ATTRIBUTION_FIELDS,
    ATTRIBUTION_FIELDSET,
    AttributionAdminMixin,
)
from apps.core.utils import stamp_attribution

from .models import InquiryForm

# Every field the public form can submit, plus the row id and the triage/audit
# columns. A lead export that drops the phone number or the budget is useless to
# the person who asked for it, so the export is deliberately exhaustive.
EXPORT_FIELDS = [
    "id", "first_name", "last_name", "email", "phone_number", "contact_mode",
    "event_type", "desired_location", "preferred_start_date", "preferred_end_date",
    "budget", "details", "status", "created_at",
]

# Excel/Sheets execute a cell beginning with any of these as a FORMULA. These
# rows are free text typed by anonymous strangers on the public internet, which
# is exactly the CSV-injection threat model — see _csv_cell.
FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_cell(value):
    """Render one field for CSV: NULL as blank, formula injection neutralised."""
    if value is None:
        return ""  # blank cell, not the literal string "None"
    if isinstance(value, datetime):
        value = timezone.localtime(value).strftime("%Y-%m-%d %H:%M:%S")
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        # Leading apostrophe forces the spreadsheet to treat the cell as text.
        return "'" + text
    return text


@admin.register(InquiryForm)
class InquiryFormAdmin(AttributionAdminMixin, admin.ModelAdmin):
    list_display = [
        "first_name", "last_name", "email", "phone_number",
        "event_type", "desired_location", "preferred_start_date", "budget",
        "status", "created_at", "updated_at", "last_updated_by_display",
    ]
    list_filter = ["status", "event_type", "contact_mode"]
    search_fields = ["first_name", "last_name", "email", "phone_number", "desired_location"]
    # Newest LEAD first, not soonest event: sorting by preferred_start_date put a
    # 2028 wedding above an inquiry that arrived this morning.
    ordering = ["-created_at"]
    # Attribution FKs are editable=False on the model, so they must be readonly
    # here — listing them in an editable fieldset raises an admin check error.
    readonly_fields = [*ATTRIBUTION_FIELDS]
    actions = ["export_as_csv"]
    fieldsets = (
        (None, {"fields": ("first_name", "last_name", "email", "phone_number", "contact_mode")}),
        ("Event", {"fields": ("event_type", "desired_location", "preferred_start_date", "preferred_end_date", "budget")}),
        ("Details", {"fields": ("details",)}),
        ("Triage", {"fields": ("status",)}),
        # created_by is always empty here (leads arrive unauthenticated); the
        # block is kept whole so attribution reads the same as everywhere else.
        ATTRIBUTION_FIELDSET,
    )

    def save_model(self, request, obj, form, change):
        """Stamp attribution on admin edits too.

        The API path goes through services.transition_inquiry_status, which
        stamps via core.utils.stamp_attribution. Admin saves bypass that
        entirely, so without this hook a status changed in the admin would show
        whoever touched it through the API *last* — attribution that is worse
        than none. Note the admin can edit every field, not just status, so here
        last_updated_by genuinely means "who last touched this row".
        """
        stamp_attribution(obj, request.user, creating=not change)
        super().save_model(request, obj, form, change)

    @admin.action(description="Export selected inquiries to CSV")
    def export_as_csv(self, request, queryset):
        """
        Export the rows the changelist handed us — i.e. the filtered selection.

        Doing this as an action rather than a standalone view is the whole
        point: staff narrow by status/event_type/search, tick, and get exactly
        that set. The date in the filename keeps repeat exports from
        overwriting each other in a downloads folder.
        """
        response = HttpResponse(content_type="text/csv")
        filename = f"inquiries-{timezone.localdate().isoformat()}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        # UTF-8 BOM. Without it Excel on Windows mangles non-ASCII — Nigerian
        # names, a ₦ in free text. It looks like a stray character otherwise.
        response.write("\ufeff")

        writer = csv.writer(response)
        writer.writerow(EXPORT_FIELDS)
        exported = 0
        for inquiry in queryset:
            writer.writerow([_csv_cell(getattr(inquiry, field)) for field in EXPORT_FIELDS])
            exported += 1

        self.message_user(request, f"Exported {exported} inquiry(ies) to CSV.")
        return response
