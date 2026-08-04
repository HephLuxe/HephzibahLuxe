"""
apps/core/serializers.py

Shared serializer building blocks.

AttributionSerializerMixin — exposes WHO created/last-changed a record as
human-readable names, and guarantees the raw actor FK ids never reach a client.
"""

from rest_framework import serializers

from .utils import user_display_name


class AttributionSerializerMixin(serializers.Serializer):
    """
    Adds `created_by_display` / `last_updated_by_display` and strips the raw
    `created_by` / `last_updated_by` FK ids from the output.

    The strip matters: a ModelSerializer with `fields = '__all__'` serializes a
    user FK as its bare integer pk, so responses used to carry
    `last_updated_by: 1` next to the display name. Marking the field read_only
    does NOT remove it from the payload — popping it in to_representation does,
    and it works no matter how the subclass declares its fields.

    Mix in to the LEFT of ModelSerializer so this to_representation runs:

        class EventSerializer(AttributionSerializerMixin, ModelSerializer): ...

    Names are resolved at read time via user_display_name, so they always show
    the account's current name (and "" once a user is deleted — the FKs are
    SET_NULL).
    """

    created_by_display = serializers.SerializerMethodField()
    last_updated_by_display = serializers.SerializerMethodField()

    def get_created_by_display(self, obj) -> str:
        return user_display_name(getattr(obj, "created_by", None))

    def get_last_updated_by_display(self, obj) -> str:
        return user_display_name(getattr(obj, "last_updated_by", None))

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Never leak raw actor ids — the *_display fields are the public surface.
        data.pop("created_by", None)
        data.pop("last_updated_by", None)
        return data
