from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.error_codes import INVALID_TRANSITION, VALIDATION_ERROR
from apps.core.permissions import IsStaffOrSuperuser, can_access_portal, enforce
from apps.core.utils import save_with_attribution

from . import services
from .models import ClientPortal, TeamMember
from .serializers import (
    ActivateEventSerializer,
    AssignTeamMemberSerializer,
    PhaseUpdateSerializer,
    PortalOverviewSerializer,
    PortalUpdateSerializer,
    TeamMemberListSerializer,
    TeamMemberSerializer,
)

# ── Envelope helper (see apps/core/exceptions.py for the exception-path half) ──

def _error(detail: str, code: str, http_status: int, errors: dict | None = None) -> Response:
    body: dict = {"detail": detail, "code": code}
    if errors:
        body["errors"] = errors
    return Response(body, status=http_status)


# ── Portal overview ─────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_portal(request):
    """
    Client: get their own portal overview.
    Staff: can pass ?portal_id=<id> to view any client's portal.
    """
    portal_id = request.query_params.get("portal_id")

    if portal_id:
        enforce(
            request.user.is_staff or request.user.is_superuser,
            "Only staff can view other users' portals.",
        )
        portal = get_object_or_404(ClientPortal, id=portal_id)
    else:
        portal = get_object_or_404(ClientPortal, user=request.user)
        enforce(can_access_portal(request.user, portal))

    serializer = PortalOverviewSerializer(portal)
    return Response(serializer.data)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def update_portal(request):
    """
    Staff only: update portal config (welcome message).
    Requires portal_id in the request body to identify which portal to update.
    """
    portal_id = request.data.get("portal_id")
    if not portal_id:
        return _error("portal_id is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    portal = get_object_or_404(ClientPortal, id=portal_id)
    serializer = PortalUpdateSerializer(portal, data=request.data, partial=True)
    if not serializer.is_valid():
        return _error("Invalid portal data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user)
    return Response(PortalOverviewSerializer(portal).data)


# ── Phase management ────────────────────────────────────────────

@api_view(["PATCH"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def update_phase(request):
    """
    Staff only: set the planning phase for a client's portal.
    Body: { "portal_id": 1, "phase": "align" }
    To advance to next phase automatically, set "advance": true instead of "phase".
    """
    portal_id = request.data.get("portal_id")
    if not portal_id:
        return _error("portal_id is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    portal = get_object_or_404(ClientPortal, id=portal_id)
    advance = request.data.get("advance", False)

    try:
        if advance:
            services.advance_phase(portal, user=request.user)
        else:
            serializer = PhaseUpdateSerializer(data=request.data)
            if not serializer.is_valid():
                return _error("Invalid phase data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)
            services.set_phase(portal, serializer.validated_data["phase"], user=request.user)
    except ValidationError as e:
        # advance_phase / set_phase raise for "no active engagement" and
        # "already at final phase" — both are illegal-state-change cases,
        # not malformed input, so they get their own code.
        return _error(str(e.detail[0]), INVALID_TRANSITION, status.HTTP_400_BAD_REQUEST)

    return Response(PortalOverviewSerializer(portal).data)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def activate_event(request):
    """
    Staff only: switch which event is the portal's active engagement.
    Body: { "portal_id": 1, "event_slug": "some-event-slug" }

    Deactivates the portal's current engagement (if any) and creates or
    reactivates one for the given event — see services.activate_engagement.
    Use this rather than deleting/recreating events when the wrong event
    ended up active; deleting an event cascade-deletes its engagement and
    everything attached to it (meetings, conversations, reminders, documents).

    Switching is non-destructive — nothing is deleted — but every meeting/
    conversation/reminder/document reads through active_engagement, so the
    previous event's content simply stops appearing, which looks identical to
    real data loss from the client's side (see docs/FAILURE_POINTS_AUDIT.md
    F5). When this call actually moves away from a different, already-active
    event, the response includes `previous_engagement_content` (a count
    breakdown of what just stopped being shown) and a `note` explaining it's
    recoverable by switching back.
    """
    from apps.events.models import Event

    portal_id = request.data.get("portal_id")
    if not portal_id:
        return _error("portal_id is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    portal = get_object_or_404(ClientPortal, id=portal_id)
    serializer = ActivateEventSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid event data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    event = Event.objects.get(slug=serializer.validated_data["event_slug"])

    previous_engagement = portal.active_engagement
    is_switching_away = bool(previous_engagement and previous_engagement.event_id != event.id)
    hidden_content = services.get_engagement_content_summary(previous_engagement) if is_switching_away else {}

    services.activate_engagement(portal, event)

    data = PortalOverviewSerializer(portal).data
    if is_switching_away:
        data["previous_engagement_content"] = hidden_content
        data["note"] = (
            "Switching the active event doesn't delete anything — the previous "
            "event's meetings, conversations, reminders, and documents (counted "
            "in previous_engagement_content) are just no longer shown while this "
            "event is active. Switch back to that event to see them again."
        )
    return Response(data)


# ── Team member profiles ────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_portal_team(request):
    """
    Client: list team members assigned to their portal.
    Staff: can pass ?portal_id=<id> to view assignments for another portal.
    """
    portal_id = request.query_params.get("portal_id")

    if portal_id:
        enforce(
            request.user.is_staff or request.user.is_superuser,
            "Only staff can view other users' portal teams.",
        )
        portal = get_object_or_404(ClientPortal, id=portal_id)
    else:
        portal = get_object_or_404(ClientPortal, user=request.user)
        enforce(can_access_portal(request.user, portal))

    assignments = portal.team_assignments.select_related("team_member").all()
    members = [a.team_member for a in assignments]

    serializer = TeamMemberListSerializer(members, many=True)
    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def assign_team_member(request):
    """
    Staff only: assign a team member to a client's portal.
    Body: { "portal_id": 1, "team_member_id": 3 }
    """
    portal_id = request.data.get("portal_id")
    if not portal_id:
        return _error("portal_id is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    portal = get_object_or_404(ClientPortal, id=portal_id)
    serializer = AssignTeamMemberSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid team member data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    services.assign_team_member(portal, serializer.validated_data["team_member_id"])
    return Response(
        {"detail": "Team member assigned."},
        status=status.HTTP_201_CREATED,
    )


@api_view(["DELETE"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def remove_team_member(request):
    """
    Staff only: remove a team member from a client's portal.
    Body: { "portal_id": 1, "team_member_id": 3 }
    """
    portal_id = request.data.get("portal_id")
    team_member_id = request.data.get("team_member_id")

    if not portal_id or not team_member_id:
        return _error("portal_id and team_member_id are required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    portal = get_object_or_404(ClientPortal, id=portal_id)
    services.remove_team_member(portal, team_member_id)
    return Response({"detail": "Team member removed."})


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def create_team_member(request):
    """
    Staff only: create a new team member profile.
    """
    serializer = TeamMemberSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid team member data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def list_team_members(request):
    """
    Staff only: list all team member profiles (not portal-specific).
    Pass ?is_default=true to list only the auto-seeded "Meet Your Team" defaults.
    """
    members = TeamMember.objects.all()
    is_default = request.query_params.get("is_default")
    if is_default is not None:
        members = members.filter(is_default=is_default.lower() in ("true", "1", "yes"))
    serializer = TeamMemberSerializer(members, many=True)
    return Response(serializer.data)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def manage_team_member(request, member_id):
    """
    Staff only: update or delete a team member profile.
    """
    member = get_object_or_404(TeamMember, id=member_id)

    if request.method == "DELETE":
        member.delete()
        return Response({"detail": "Team member deleted."})

    serializer = TeamMemberSerializer(member, data=request.data, partial=True)
    if not serializer.is_valid():
        return _error("Invalid team member data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user)
    return Response(serializer.data)
