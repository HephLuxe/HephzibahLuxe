from rest_framework import serializers

from apps.core.serializers import AttributionSerializerMixin
from .models import Meeting, MeetingNotes, MeetingPrepItem, PrepItemField, PrepItemResponse, PrepItemFileUpload
from .services import field_is_answered

class PrepItemFileUploadSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = PrepItemFileUpload
        fields = ["id", "filename", "file_url", "uploaded_at"]

    def get_file_url(self, obj: PrepItemFileUpload) -> str | None:
        request = self.context.get("request")
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        return None

class PrepItemResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrepItemResponse
        fields = ["id", "text_value", "submitted_at"]


class PrepItemFieldSerializer(serializers.ModelSerializer):
    field_type_display = serializers.CharField(source="get_field_type_display", read_only=True)
    response = PrepItemResponseSerializer(read_only=True)
    uploads = PrepItemFileUploadSerializer(many=True, read_only=True)
    # Each field carries its own completion — answered vs not — for required
    # and optional fields alike (see services.field_is_answered).
    is_completed = serializers.SerializerMethodField()

    def get_is_completed(self, obj: PrepItemField) -> bool:
        return field_is_answered(obj)

    class Meta:
        model = PrepItemField
        fields = [
            "id", "field_type", "field_type_display",
            "label", "helper_text", "is_required", "order",
            "is_completed", "response", "uploads",
        ]

class PrepItemFieldCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrepItemField
        fields = ["field_type", "label", "helper_text", "is_required", "order"]

class PrepItemFieldUpdateSerializer(serializers.ModelSerializer):
    """
    Staff edit of a field (partial). field_type is editable — changing it wipes
    the field's existing answer (handled in services.update_prep_field).
    """
    class Meta:
        model = PrepItemField
        fields = ["field_type", "label", "helper_text", "is_required", "order"]

class MeetingPrepItemSerializer(serializers.ModelSerializer):
    fields = PrepItemFieldSerializer(many=True, read_only=True)
    # Two counters: `required_fields` drives is_completed and the Figma "N of M"
    # badge; `optional_fields` is informational and never affects completion.
    # An all-optional item reports total=0 required and is gated on its optional
    # fields instead (see services.sync_prep_item_completion).
    required_fields = serializers.SerializerMethodField()
    optional_fields = serializers.SerializerMethodField()

    def _counts(self, obj: MeetingPrepItem, *, required: bool) -> dict:
        fields = [f for f in obj.fields.all() if f.is_required == required]
        return {
            "answered": sum(1 for f in fields if field_is_answered(f)),
            "total": len(fields),
        }

    def get_required_fields(self, obj: MeetingPrepItem) -> dict:
        return self._counts(obj, required=True)

    def get_optional_fields(self, obj: MeetingPrepItem) -> dict:
        return self._counts(obj, required=False)

    class Meta:
        model = MeetingPrepItem
        fields = [
            "id", "title", "description", "is_completed", "order",
            "fields", "required_fields", "optional_fields",
        ]

class MeetingPrepItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeetingPrepItem
        fields = ["title", "description", "order"]

class MeetingPrepItemUpdateSerializer(serializers.ModelSerializer):
    """Staff edit of a prep item's own attributes (partial). Fields are managed
    through the dedicated field endpoints, not here."""
    class Meta:
        model = MeetingPrepItem
        fields = ["title", "description", "order"]

class MeetingNotesSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = MeetingNotes
        fields = ["id", "summary", "key_discussions", "key_decisions", "action_items", "created_at", "created_by_display", "last_updated_by_display",
        ]


class MeetingListSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    phase_display = serializers.CharField(source="get_phase_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    prep_items_count = serializers.SerializerMethodField()
    completed_prep_items_count = serializers.SerializerMethodField()

    def get_prep_items_count(self, obj: Meeting) -> int:
        return obj.prep_items.count()

    def get_completed_prep_items_count(self, obj: Meeting) -> int:
        return obj.prep_items.filter(is_completed=True).count()

    class Meta:
        model = Meeting
        fields = [
            "id", "title", "date", "time", "duration_minutes",
            "status", "status_display", "phase", "phase_display",
            "preparation_required", "prep_items_count", "completed_prep_items_count",
            "created_by_display", "last_updated_by_display",
        ]


class MeetingDetailSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    phase_display = serializers.CharField(source="get_phase_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    prep_items = MeetingPrepItemSerializer(many=True, read_only=True)
    prep_items_count = serializers.SerializerMethodField()
    completed_prep_items_count = serializers.SerializerMethodField()
    notes = MeetingNotesSerializer(read_only=True)

    def get_prep_items_count(self, obj: Meeting) -> int:
        return obj.prep_items.count()

    def get_completed_prep_items_count(self, obj: Meeting) -> int:
        return obj.prep_items.filter(is_completed=True).count()

    class Meta:
        model = Meeting
        fields = [
            "id", "title", "date", "time", "duration_minutes",
            "meeting_url", "status", "status_display", "phase", "phase_display",
            "description", "preparation_required",
            "prep_items", "prep_items_count", "completed_prep_items_count", "notes",
            "created_at", "updated_at",
            "created_by_display", "last_updated_by_display",
        ]


class MeetingCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Meeting
        fields = [
            "title", "date", "time", "duration_minutes",
            "meeting_url", "phase", "description", "preparation_required",
        ]


class MeetingUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Meeting
        fields = ["title", "date", "time", "duration_minutes", "meeting_url", "description"]