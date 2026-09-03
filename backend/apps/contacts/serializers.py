from rest_framework import serializers

from apps.core.serializers import AttributionSerializerMixin, PrivateFileURLField
from apps.core.uploads import validate_photo

from .models import EventContact

# AttributionSerializerMixin supplies created_by_display / last_updated_by_display
# (see docs/FAILURE_POINTS_AUDIT.md F11 for why these are FKs resolved at read
# time, not frozen name strings) and strips the raw actor FK ids from responses.


class EventContactSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    """Full detail — used for create and single-contact reads."""
    # Contact photos moved to the private storage tier, so reads expose the
    # mint path under `photo_url` and the raw `photo` field is no longer
    # serialized at all. Uploads still target `photo`, on
    # EventContactCreateSerializer below.
    photo_url = PrivateFileURLField("contact-photo")
    category_display = serializers.CharField(
        source="get_category_display", read_only=True
    )
    preferred_method_display = serializers.CharField(
        source="get_preferred_method_display", read_only=True
    )

    class Meta:
        model = EventContact
        fields = [
            "id", "event", "event_day", "category", "category_display",
            "name", "role", "phone", "email",
            "preferred_method", "preferred_method_display",
            "photo_url",
            "created_at", "created_by_display",
            "updated_at", "last_updated_by_display",
        ]
        read_only_fields = ["id", "event", "created_at", "updated_at"]


class EventContactListSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    """Minimal — used when listing contacts grouped by category."""
    photo_url = PrivateFileURLField("contact-photo")
    category_display = serializers.CharField(
        source="get_category_display", read_only=True
    )
    preferred_method_display = serializers.CharField(
        source="get_preferred_method_display", read_only=True
    )

    class Meta:
        model = EventContact
        fields = [
            "id", "event_day", "category", "category_display",
            "name", "role", "phone", "email",
            "preferred_method", "preferred_method_display",
            "photo_url", "created_by_display", "last_updated_by_display",
        ]


class EventContactCreateSerializer(serializers.ModelSerializer):
    """Input-only — used when staff creates a contact (event set from URL)."""

    def validate(self, data: dict) -> dict:
        # event_day required check (belt-and-suspenders since model enforces it)
        if not data.get("event_day"):
            raise serializers.ValidationError({"event_day": "An event day is required."})
        email = data.get("email", "")
        if email:
            qs = EventContact.objects.filter(
                event=self.context["event"],
                event_day=data.get("event_day"),
                category=data.get("category"),
                email=email,
            )
            # Exclude the current instance when updating (PATCH)
            if self.instance:
                qs = qs.exclude(id=self.instance.id)
            if qs.exists():
                raise serializers.ValidationError(
                    "A contact with this email already exists in this category for this event day."
                )
        return data

    class Meta:
        model = EventContact
        fields = [
            "event_day", "category", "name", "role", "phone", "email",
            "preferred_method", "photo",
        ]

    def validate_photo(self, value):
        return validate_photo(value)