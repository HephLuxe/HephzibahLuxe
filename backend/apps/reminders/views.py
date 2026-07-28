"""
apps/reminders/views.py

Reference implementation of the new conventions (audit/plan Part 7):
  * thin views — logic delegated to services.py
  * standard error envelope {detail, code, errors?} via _error()
  * machine-readable codes from apps.core.error_codes
  * type-annotated helpers

Routes (all under /api/v1/):
  GET    /reminders/                     list (client: own; staff: ?portal_id=)
  POST   /reminders/create/              staff — body: portal_id + fields
  GET    /reminders/<uuid>/              retrieve (owner or staff)
  PATCH  /reminders/<uuid>/              staff — edit
  DELETE /reminders/<uuid>/              staff — delete
  PATCH  /reminders/<uuid>/complete/     owner or staff — toggle completion
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.error_codes import (
    NOT_FOUND,
    PERMISSION_DENIED,
    VALIDATION_ERROR,
)
from apps.core.permissions import IsStaffOrSuperuser, is_staff_or_superuser
from apps.portal.models import ClientPortal

from . import services
from .models import Reminder
from .serializers import (
    ReminderCreateSerializer,
    ReminderSerializer,
    ReminderUpdateSerializer,
)


# ── Envelope helper ─────────────────────────────────────────────

def _error(detail: str, code: str, http_status: int, errors: dict | None = None) -> Response:
    body: dict = {"detail": detail, "code": code}
    if errors:
        body["errors"] = errors
    return Response(body, status=http_status)


# ── Lookups (return the object or an error Response) ─────────────

def _resolve_portal(request: Request):
    """
    Client → their own portal. Staff → any portal via ?portal_id=<uuid>.
    Returns (portal, None) or (None, error_response).
    """
    portal_id = request.query_params.get("portal_id")
    if portal_id:
        if not is_staff_or_superuser(request.user):
            return None, _error("Only staff can view other clients' reminders.", PERMISSION_DENIED, 403)
        try:
            portal = ClientPortal.objects.filter(id=portal_id).first()
        except (ValueError, DjangoValidationError):
            return None, _error("Invalid portal_id.", VALIDATION_ERROR, 400)
        if portal is None:
            return None, _error("Portal not found.", NOT_FOUND, 404)
        return portal, None

    portal = ClientPortal.objects.filter(user=request.user).first()
    if portal is None:
        return None, _error("No portal found for this user.", NOT_FOUND, 404)
    return portal, None


def _get_reminder(reminder_id: str):
    """Returns (reminder, None) or (None, error_response)."""
    try:
        reminder = Reminder.objects.filter(id=reminder_id).select_related(
            "engagement__portal__user"
        ).first()
    except (ValueError, DjangoValidationError):
        return None, _error("Invalid reminder id.", VALIDATION_ERROR, 400)
    if reminder is None:
        return None, _error("Reminder not found.", NOT_FOUND, 404)
    return reminder, None


def _can_access_reminder(user, reminder: Reminder) -> bool:
    if is_staff_or_superuser(user):
        return True
    owner = reminder.engagement.portal.user if reminder.engagement else None
    return owner == user


# ── Endpoints ───────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_reminders(request: Request) -> Response:
    """
    List reminders for the portal's active engagement.
    Filters: ?status=pending|completed|all (default pending)
    Sort:    ?sort=priority|due|newest|oldest (default priority)
    """
    portal, err = _resolve_portal(request)
    if err:
        return err

    status_filter = request.query_params.get("status", services.STATUS_PENDING)
    sort = request.query_params.get("sort", services.SORT_PRIORITY)

    reminders = services.list_reminders(
        portal.active_engagement, status=status_filter, sort=sort
    )
    return Response(ReminderSerializer(reminders, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def create_reminder(request: Request) -> Response:
    """
    Staff only. Body must include portal_id + reminder fields.
    Optional "engagement_id": target a specific (possibly inactive) engagement
    instead of the portal's active one — lets staff pre-stage a future
    event's reminders before switching to it (see
    docs/FAILURE_POINTS_AUDIT.md F3 addendum).
    """
    portal_id = request.data.get("portal_id")
    if not portal_id:
        return _error("portal_id is required.", VALIDATION_ERROR, 400)

    try:
        portal = ClientPortal.objects.filter(id=portal_id).first()
    except (ValueError, DjangoValidationError):
        return _error("Invalid portal_id.", VALIDATION_ERROR, 400)
    if portal is None:
        return _error("Portal not found.", NOT_FOUND, 404)

    engagement_id = request.data.get("engagement_id")
    if engagement_id:
        engagement = portal.engagements.filter(id=engagement_id).first()
        if engagement is None:
            return _error("Engagement not found for this portal.", NOT_FOUND, 404)
    else:
        engagement = portal.active_engagement
        if engagement is None:
            return _error("This portal has no active engagement.", VALIDATION_ERROR, 400)

    serializer = ReminderCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid reminder data.", VALIDATION_ERROR, 400, errors=serializer.errors)

    reminder = services.create_reminder(engagement, request.user, serializer.validated_data)
    return Response(ReminderSerializer(reminder).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def reminder_detail(request: Request, reminder_id: str) -> Response:
    """GET: owner or staff. PATCH/DELETE: staff only."""
    reminder, err = _get_reminder(reminder_id)
    if err:
        return err

    if request.method == "GET":
        if not _can_access_reminder(request.user, reminder):
            return _error("You do not have permission to view this reminder.", PERMISSION_DENIED, 403)
        return Response(ReminderSerializer(reminder).data)

    # PATCH / DELETE — staff only
    if not is_staff_or_superuser(request.user):
        return _error("Only staff can modify reminders.", PERMISSION_DENIED, 403)

    if request.method == "DELETE":
        reminder.delete()
        return Response({"detail": "Reminder deleted."})

    serializer = ReminderUpdateSerializer(reminder, data=request.data, partial=True)
    if not serializer.is_valid():
        return _error("Invalid reminder data.", VALIDATION_ERROR, 400, errors=serializer.errors)

    # Goes through the service (not serializer.save()) so a retargeted deep
    # link is re-validated against this reminder's engagement — see
    # services.update_reminder / resolve_target. Its ValidationError is a
    # genuine input-validation case and propagates to custom_exception_handler.
    reminder = services.update_reminder(reminder, serializer.validated_data, updated_by=request.user)
    return Response(ReminderSerializer(reminder).data)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def complete_reminder(request: Request, reminder_id: str) -> Response:
    """
    Toggle completion. Owner (client) or staff.
    Body: {"is_completed": true|false} — defaults to true.
    """
    reminder, err = _get_reminder(reminder_id)
    if err:
        return err

    if not _can_access_reminder(request.user, reminder):
        return _error("You do not have permission to update this reminder.", PERMISSION_DENIED, 403)

    is_completed = request.data.get("is_completed", True)
    if not isinstance(is_completed, bool):
        return _error("is_completed must be a boolean.", VALIDATION_ERROR, 400)

    reminder = services.set_completed(reminder, is_completed, updated_by=request.user)
    return Response(ReminderSerializer(reminder).data)
