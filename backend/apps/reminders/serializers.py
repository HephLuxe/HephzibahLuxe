"""
apps/reminders/serializers.py

Read / create / update shapes for reminders. Business rules (who may set what,
and whether a deep-link target is actually this client's to link) live in the
views + services, not here — see services.resolve_target.
"""

from rest_framework import serializers

from apps.core import deeplinks
from apps.core.serializers import AttributionSerializerMixin

from .models import Reminder


class ReminderSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    """
    Full read shape returned to clients and staff.

    `link_url` / `link_label` are *derived* (see Reminder.resolved_link_url):
    when the reminder points at an object, the route comes from
    core.deeplinks, so renaming a portal route never leaves stored links
    dangling. `target_type` / `target_id` are exposed so a caller can act on
    the target directly instead of parsing it back out of the URL.
    """

    priority_display = serializers.CharField(source="get_priority_display", read_only=True)
    link_url = serializers.CharField(source="resolved_link_url", read_only=True)
    link_label = serializers.CharField(source="resolved_link_label", read_only=True)
    target_type = serializers.CharField(read_only=True)
    target_id = serializers.CharField(source="target_object_id", read_only=True)

    class Meta:
        model = Reminder
        fields = [
            "id",
            "title",
            "description",
            "priority",
            "priority_display",
            "due_date",
            "is_completed",
            "completed_at",
            "target_type",
            "target_id",
            "link_url",
            "link_label",
            "order",
            "created_at",
            "created_by_display",
            "updated_at",
            "last_updated_by_display",
        ]
        read_only_fields = fields


class _TargetFieldsMixin(metaclass=serializers.SerializerMetaclass):
    """
    The write half of the deep link.

    Staff name *what* the reminder is about (`target_type` + `target_id`) and
    the URL is derived on read. `link_url` remains writable for links with no
    object behind them (a static portal page, an external URL); when a target
    is set it is ignored for display.
    """

    target_type = serializers.ChoiceField(
        choices=sorted(deeplinks.TARGET_TYPES),
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="What the reminder is about, e.g. 'conversation', 'prep_item', 'invoice'.",
    )
    target_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="The target object's id. Must belong to this client's engagement.",
    )


class ReminderCreateSerializer(_TargetFieldsMixin, serializers.ModelSerializer):
    """Staff-only create. engagement + created_by are set by the view."""

    class Meta:
        model = Reminder
        fields = [
            "title",
            "description",
            "priority",
            "due_date",
            "target_type",
            "target_id",
            "link_url",
            "link_label",
            "order",
        ]


class ReminderUpdateSerializer(_TargetFieldsMixin, serializers.ModelSerializer):
    """
    Staff-only edit. All fields optional (partial update).
    Send `"target_type": null` to clear an existing target.
    """

    class Meta:
        model = Reminder
        fields = [
            "title",
            "description",
            "priority",
            "due_date",
            "is_completed",
            "target_type",
            "target_id",
            "link_url",
            "link_label",
            "order",
        ]
