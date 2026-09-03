"""
apps/core/serializers.py

Shared serializer building blocks.

AttributionSerializerMixin — exposes WHO created/last-changed a record as
human-readable names, and guarantees the raw actor FK ids never reach a client.
"""

from django.core.exceptions import ImproperlyConfigured
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


class PrivateFileURLField(serializers.Field):
    """
    Emits the ``/api/v1/files/<type>/<id>/`` mint path for a private file field,
    in place of the raw signed storage URL.

    Why serializers stopped emitting the storage URL directly: that URL carried a
    one-hour signature minted at *serialization* time, so it went stale in an
    open tab, outlived a revoked permission, and kept working for anyone it was
    forwarded to. The path this returns never expires — it is safe to cache in
    the frontend — while the URL it mints on demand lives 60 seconds and is
    re-authorised on every call. Full reasoning in apps/core/filelinks.py.

    ``source="*"`` because the value is derived from the whole instance (the
    object's pk plus its registered type), not from one attribute. Read-only by
    construction: writes still go to the real file field, which is why upload
    endpoints are unaffected by this.

    Returns None for an empty file, matching what DRF's FileField does, so a
    caller can tell "no file" from "a file it must go and fetch".
    """

    def __init__(self, file_type: str, **kwargs):
        self.file_type = file_type
        kwargs["read_only"] = True
        kwargs.setdefault("source", "*")
        super().__init__(**kwargs)

    def to_representation(self, instance):
        from apps.core.filelinks import file_url_path, spec_for_type

        spec = spec_for_type(self.file_type)
        if spec is None:
            # An unregistered type would silently emit a path that 404s. Better
            # to fail at import/serialization than to ship a dead link.
            raise ImproperlyConfigured(
                f"PrivateFileURLField('{self.file_type}') is not registered in "
                "apps.core.filelinks.FILE_TYPES."
            )
        if not getattr(instance, spec.field, None):
            return None
        return file_url_path(self.file_type, instance.pk)
