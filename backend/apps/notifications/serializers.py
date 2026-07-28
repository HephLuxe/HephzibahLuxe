"""
apps/notifications/serializers.py

Read-only shape for the in-portal notification history. Notifications are
created by services.queue_notification, never through the API, so there is no
create/update serializer here.
"""

from rest_framework import serializers

from .models import Notification, NotificationType


class NotificationHistorySerializer(serializers.ModelSerializer):
    """
    One delivered notification, as the client sees it in their portal.

    Deliberately omits three model fields:

      * `context`       — the Brevo template variables. For the auth templates
                          it holds a `temporary_password` / reset `code`
                          (see accounts/utils.py), so it must never be
                          serialised. Those types are filtered out of the
                          history entirely as well (see views.AUTH_ONLY_TYPES),
                          but omitting the field is the belt to that braces.
      * `error_message` — internal delivery diagnostics; belongs in the admin,
                          not in a client payload.
      * `attempt_count` — same reasoning.

    `type_display` gives the human label ("Payment due") for the raw
    `template_name` ("payment_due") so the frontend doesn't hardcode the map.
    """

    type_display = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id", "template_name", "type_display", "subject",
            "status", "sent_at", "created_at",
        ]
        read_only_fields = fields

    def get_type_display(self, obj) -> str:
        try:
            return NotificationType(obj.template_name).label
        except ValueError:
            # A template_name not in the enum (legacy row / newly added type
            # before the enum caught up) — fall back to the raw value rather
            # than 500 on a history read.
            return obj.template_name
