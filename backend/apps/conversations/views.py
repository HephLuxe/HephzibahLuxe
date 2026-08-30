from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.error_codes import NOT_FOUND, VALIDATION_ERROR
from apps.core.permissions import IsStaffOrSuperuser, can_access_portal, enforce
from apps.core.utils import save_with_attribution
from apps.portal.models import ClientPortal, PlanningPhase

from . import services
from .models import Conversation, ConversationTag
from .serializers import (
    ConversationCreateSerializer,
    ConversationDetailSerializer,
    ConversationListSerializer,
    ConversationUpdateSerializer,
)

# ── Envelope helper (see apps/core/exceptions.py for the exception-path half) ──

def _error(detail: str, code: str, http_status: int, errors: dict | None = None) -> Response:
    body: dict = {"detail": detail, "code": code}
    if errors:
        body["errors"] = errors
    return Response(body, status=http_status)


def _resolve_portal(request):
    """Client -> own portal. Staff -> ?portal_id=<id>. Returns (portal, None) or (None, error_response)."""
    portal_id = request.query_params.get("portal_id")
    if portal_id:
        enforce(
            request.user.is_staff or request.user.is_superuser,
            "Only staff can view other clients' conversations.",
        )
        return get_object_or_404(ClientPortal, id=portal_id), None
    portal = get_object_or_404(ClientPortal, user=request.user)
    enforce(can_access_portal(request.user, portal))
    return portal, None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_conversations(request):
    """
    Client: list their own conversations.
    Staff: pass ?portal_id=<id> to view any client's conversations.
    Filterable by ?phase= and ?tag=
    Sorted by ?sort=oldest (default newest)
    """
    portal, _ = _resolve_portal(request)

    engagement = portal.active_engagement
    if not engagement:
        return Response([])
    conversations = engagement.conversations.all()

    phase_filter = request.query_params.get("phase")
    if phase_filter:
        conversations = conversations.filter(phase=phase_filter)

    tag_filter = request.query_params.get("tag")
    if tag_filter:
        conversations = conversations.filter(tags__contains=[tag_filter])

    sort = request.query_params.get("sort", "newest")
    if sort == "oldest":
        conversations = conversations.order_by("created_at")
    else:
        conversations = conversations.order_by("-created_at")

    # Pagination — default 4 per phase, "load all" passes ?limit=all
    limit = request.query_params.get("limit")
    if limit != "all":
        conversations = conversations[:4]

    serializer = ConversationListSerializer(conversations, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_tags(request):
    """
    The "Filter by" checkbox options — served from the backend enum so the
    frontend never hardcodes a list that can drift out of sync with what
    create/update actually accepts (see ConversationTag).
    """
    return Response([{"value": t.value, "label": t.label} for t in ConversationTag])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def phase_summary(request):
    """
    Returns conversation counts per phase for the current portal, plus which
    phase is the engagement's current one (drives the "Current Phase" badge
    and default-expanded accordion section).
    Staff: pass ?portal_id=<id>.
    """
    portal, _ = _resolve_portal(request)

    engagement = portal.active_engagement
    current_phase = engagement.current_phase if engagement else None

    summary = []
    for phase in PlanningPhase:
        count = engagement.conversations.filter(phase=phase.value).count() if engagement else 0
        summary.append({
            "phase": phase.value,
            "phase_display": phase.label,
            "is_current_phase": phase.value == current_phase,
            "count": count,
        })

    return Response(summary)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def create_conversation(request):
    """
    Staff only. Body must include portal_id.
    Optional "engagement_id": target a specific (possibly inactive) engagement
    instead of the portal's active one — lets staff pre-stage a future
    event's conversations before switching to it (see
    docs/FAILURE_POINTS_AUDIT.md F3 addendum).
    """
    portal_id = request.data.get("portal_id")
    if not portal_id:
        return _error("portal_id is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    portal = get_object_or_404(ClientPortal, id=portal_id)
    serializer = ConversationCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid conversation data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    tags = serializer.validated_data.get("tags", [])
    services.validate_tags(tags)  # raises ValidationError -> generic VALIDATION_ERROR envelope

    # Engagement must be resolved BEFORE the links are validated: a targeted
    # link is only legal if its object belongs to this engagement, and
    # validate_links cannot make that check without it.
    engagement_id = request.data.get("engagement_id")
    if engagement_id:
        engagement = portal.engagements.filter(id=engagement_id).first()
        if not engagement:
            return _error("Engagement not found for this portal.", NOT_FOUND, status.HTTP_404_NOT_FOUND)
    else:
        engagement = portal.active_engagement
        if not engagement:
            return _error("This portal has no active engagement.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    links = serializer.validated_data.get("links", [])
    services.validate_links(links, engagement)

    conversation = save_with_attribution(serializer, request.user, engagement=engagement)
    return Response(
        ConversationDetailSerializer(conversation).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def conversation_detail(request, conversation_id):
    """
    GET: staff or portal owner.
    PATCH/DELETE: staff only.
    """
    conversation = get_object_or_404(Conversation, id=conversation_id)

    if request.method == "GET":
        enforce(
            request.user.is_staff or request.user.is_superuser
            or conversation.engagement.portal.user == request.user,
            "You do not have permission to view this conversation.",
        )
        return Response(ConversationDetailSerializer(conversation).data)

    enforce(
        request.user.is_staff or request.user.is_superuser,
        "Only staff can modify conversations.",
    )

    if request.method == "DELETE":
        conversation.delete()
        return Response({"detail": "Conversation deleted."})

    serializer = ConversationUpdateSerializer(conversation, data=request.data, partial=True)
    if not serializer.is_valid():
        return _error("Invalid conversation data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    tags = serializer.validated_data.get("tags", conversation.tags)
    services.validate_tags(tags)  # raises ValidationError -> generic VALIDATION_ERROR envelope
    links = serializer.validated_data.get("links", conversation.links)
    # Same engagement check as create — an edit must not be able to smuggle in
    # a link to another client's object.
    services.validate_links(links, conversation.engagement)
    save_with_attribution(serializer, request.user)
    conversation.refresh_from_db()
    return Response(ConversationDetailSerializer(conversation).data)
