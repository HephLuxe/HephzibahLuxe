from django import forms
from django.contrib import admin
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.core.admin import ATTRIBUTION_FIELDS, AttributionAdminMixin

from . import services
from .models import Conversation


class ConversationAdminForm(forms.ModelForm):
    """Runs the same tag/link validation the API uses (services.validate_tags/validate_links)."""

    class Meta:
        model = Conversation
        fields = "__all__"

    def clean_tags(self):
        tags = self.cleaned_data.get("tags") or []
        try:
            return services.validate_tags(tags)
        except DRFValidationError as e:
            raise forms.ValidationError(e.detail)

    def clean(self):
        # Links are validated in clean(), not clean_links(), because a targeted
        # link can only be checked against the engagement — which isn't in
        # cleaned_data until every field has been cleaned.
        cleaned = super().clean()
        links = cleaned.get("links") or []
        engagement = cleaned.get("engagement")
        try:
            services.validate_links(links, engagement)
        except DRFValidationError as e:
            raise forms.ValidationError({"links": e.detail})
        return cleaned


@admin.register(Conversation)
class ConversationAdmin(AttributionAdminMixin, admin.ModelAdmin):
    form = ConversationAdminForm
    list_display = ["conversation_with", "get_client", "phase", "contact_method", "created_at", "created_by_display", "last_updated_by_display"]
    list_filter = ["phase", "contact_method"]
    search_fields = ["conversation_with", "title", "engagement__portal__user__email"]
    raw_id_fields = ["engagement"]
    ordering = ["-created_at"]
    readonly_fields = [*ATTRIBUTION_FIELDS]

    @admin.display(description="Client")
    def get_client(self, obj):
        if obj.engagement is None:
            return "-"
        return obj.engagement.portal.user.email
