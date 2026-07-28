"""
apps/notifications/views.py

In-portal notification history — the read side of what until now was an
email-only pipeline. Notifications are still created exclusively by
services.queue_notification; nothing here writes.
"""

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.error_codes import PERMISSION_DENIED, VALIDATION_ERROR
from apps.core.permissions import is_staff_or_superuser
from apps.portal.models import ClientPortal

from .models import Notification, NotificationStatus, NotificationType
from .serializers import NotificationHistorySerializer

# Auth emails are excluded from the history: they're account-security messages
# rather than portal activity, and their `context` carries a temporary password
# / reset code. Filtering them here means even a staff view can't surface them.
AUTH_ONLY_TYPES = [NotificationType.USER_CREDENTIALS, NotificationType.PASSWORD_RESET]

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _error(detail: str, code: str, http_status: int) -> Response:
    return Response({"detail": detail, "code": code}, status=http_status)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_notifications(request: Request) -> Response:
    """
    The notification history.

    Client — their own delivered notifications.
    Staff  — pass ?portal_id=<uuid> to read a specific client's history, or omit
             it for a platform-wide feed (ops view).

    Query params (all optional):
      portal_id=<uuid>   staff only; whose history to read
      type=<name>        filter by template_name (repeatable)
      status=<name>      staff only; queued|sent|failed. Clients always see
                         only `sent` — a queued or failed notification never
                         reached them, so listing it as history would be a lie.
      limit=<n>          page size, default 50, max 200

    Auth emails (credentials / password reset) are never included.
    """
    staff = is_staff_or_superuser(request.user)
    qs = Notification.objects.exclude(template_name__in=AUTH_ONLY_TYPES)

    portal_id = request.query_params.get("portal_id")
    if portal_id:
        if not staff:
            return _error(
                "Only staff can read another client's notifications.",
                PERMISSION_DENIED, status.HTTP_403_FORBIDDEN,
            )
        portal = get_object_or_404(ClientPortal, id=portal_id)
        qs = qs.filter(_recipient_q(portal.user))
    elif not staff:
        qs = qs.filter(_recipient_q(request.user))
    # staff + no portal_id -> platform-wide feed, left unfiltered

    types = request.query_params.getlist("type")
    if types:
        qs = qs.filter(template_name__in=types)

    if staff:
        wanted_status = request.query_params.get("status")
        if wanted_status:
            if wanted_status not in NotificationStatus.values:
                return _error(
                    f"Invalid status. Allowed: {', '.join(NotificationStatus.values)}.",
                    VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(status=wanted_status)
    else:
        qs = qs.filter(status=NotificationStatus.SENT)

    try:
        limit = int(request.query_params.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        return _error("limit must be an integer.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)
    limit = max(1, min(limit, MAX_LIMIT))

    total = qs.count()
    rows = qs.order_by("-created_at")[:limit]
    serializer = NotificationHistorySerializer(rows, many=True)
    return Response({"count": total, "limit": limit, "results": serializer.data})


def _recipient_q(user) -> Q:
    """Notifications belonging to a user. recipient_user is SET_NULL, so fall
    back to the email the notification was actually addressed to."""
    return Q(recipient_user=user) | Q(recipient_email__iexact=user.email)
