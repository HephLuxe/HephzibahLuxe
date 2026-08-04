from rest_framework import serializers

from apps.core.serializers import AttributionSerializerMixin

from . import services
from .models import Conversation


class ConversationCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ["phase", "conversation_with", "contact_method", "title", "body", "tags", "links"]


class ConversationListSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    contact_method_display = serializers.CharField(
        source="get_contact_method_display", read_only=True
    )
    phase_display = serializers.CharField(source="get_phase_display", read_only=True)
    # Resolved, not raw: a targeted link stores {target_type, target_id} and its
    # url is derived here (services.resolve_links → core.deeplinks). Entries
    # whose target has been deleted are dropped rather than served as a pill
    # that 404s.
    links = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ["id", "phase", "phase_display", "conversation_with","contact_method",
            "contact_method_display", "title", "tags", "links", "created_at",            "created_by_display", "last_updated_by_display",
        ]

    def get_links(self, obj) -> list[dict]:
        return services.resolve_links(obj)


class ConversationDetailSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    contact_method_display = serializers.CharField(source="get_contact_method_display", read_only=True)
    phase_display = serializers.CharField(source="get_phase_display", read_only=True)
    links = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ["id", "phase", "phase_display", "conversation_with", "contact_method", "contact_method_display",
            "title", "body", "tags", "links", "created_at", "updated_at", "created_by_display", "last_updated_by_display",
        ]

    def get_links(self, obj) -> list[dict]:
        return services.resolve_links(obj)


class ConversationUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ["conversation_with","phase", "contact_method", "title", "body", "tags", "links"]
