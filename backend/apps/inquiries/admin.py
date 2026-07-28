from django.contrib import admin

from .models import InquiryForm


@admin.register(InquiryForm)
class InquiryFormAdmin(admin.ModelAdmin):
    list_display = [
        "first_name", "last_name", "email", "phone_number",
        "event_type", "desired_location", "preferred_start_date", "budget",
    ]
    list_filter = ["event_type", "contact_mode"]
    search_fields = ["first_name", "last_name", "email", "phone_number", "desired_location"]
    ordering = ["-preferred_start_date"]
    fieldsets = (
        (None, {"fields": ("first_name", "last_name", "email", "phone_number", "contact_mode")}),
        ("Event", {"fields": ("event_type", "desired_location", "preferred_start_date", "preferred_end_date", "budget")}),
        ("Details", {"fields": ("details",)}),
    )
