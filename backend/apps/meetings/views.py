from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.utils import save_with_attribution
from rest_framework import status
from rest_framework.exceptions import ValidationError

from apps.core.error_codes import INVALID_TRANSITION, NOT_FOUND, VALIDATION_ERROR
from apps.core.permissions import IsStaffOrSuperuser, can_access_portal, enforce, is_staff_or_superuser
from apps.portal.models import ClientPortal, PlanningPhase
from .models import (
    Meeting, MeetingNotes, MeetingPrepItem, MeetingStatus, PrepItemField, PrepItemFileUpload,
)
from .serializers import (
    MeetingCreateSerializer,
    MeetingDetailSerializer,
    MeetingListSerializer,
    MeetingNotesSerializer,
    MeetingPrepItemSerializer,
    MeetingPrepItemCreateSerializer,
    MeetingPrepItemUpdateSerializer,
    PrepItemFieldSerializer,
    PrepItemFieldCreateSerializer,
    PrepItemFieldUpdateSerializer,
    MeetingUpdateSerializer,
)
from . import services


# ── Envelope helper (see apps/core/exceptions.py for the exception-path half) ──

def _error(detail: str, code: str, http_status: int, errors: dict | None = None) -> Response:
    body: dict = {"detail": detail, "code": code}
    if errors:
        body["errors"] = errors
    return Response(body, status=http_status)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_meetings(request):
    """
    Client: list their own meetings.
    Staff: pass ?portal_id=<id> to list any client's meetings.
    Filterable by ?status= and ?phase=
    """
    portal_id = request.query_params.get("portal_id")

    if portal_id:
        enforce(
            request.user.is_staff or request.user.is_superuser,
            "Only staff can view other clients' meetings.",
        )
        portal = get_object_or_404(ClientPortal, id=portal_id)
    else:
        portal = get_object_or_404(ClientPortal, user=request.user)
        enforce(can_access_portal(request.user, portal))

    engagement = portal.active_engagement
    if not engagement:
        return Response([])
    meetings = engagement.meetings.all()

    status_filter = request.query_params.get("status")
    if status_filter:
        meetings = meetings.filter(status=status_filter)

    phase_filter = request.query_params.get("phase")
    if phase_filter:
        meetings = meetings.filter(phase=phase_filter)

    serializer = MeetingListSerializer(meetings, many=True)
    return Response(serializer.data)


# Meetings that are RESCHEDULED or ACTIVE still render under the "Upcoming"
# tab in the Figma (a card can carry a "Rescheduled" pill while still living
# in the Upcoming bucket) — only COMPLETED/CANCELLED get their own tab.
UPCOMING_BUCKET_STATUSES = [MeetingStatus.UPCOMING, MeetingStatus.RESCHEDULED, MeetingStatus.ACTIVE]


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def phase_summary(request):
    """
    Returns, per phase: which phase is current, and meeting counts for the
    All / Upcoming / Completed / Cancelled tabs — drives the phase accordion
    + status tabs on the Meetings page without the frontend re-deriving
    counts client-side.
    Staff: pass ?portal_id=<id>.
    """
    portal_id = request.query_params.get("portal_id")

    if portal_id:
        enforce(
            request.user.is_staff or request.user.is_superuser,
            "Only staff can view other clients' meetings.",
        )
        portal = get_object_or_404(ClientPortal, id=portal_id)
    else:
        portal = get_object_or_404(ClientPortal, user=request.user)
        enforce(can_access_portal(request.user, portal))

    engagement = portal.active_engagement
    current_phase = engagement.current_phase if engagement else None

    summary = []
    for phase in PlanningPhase:
        qs = engagement.meetings.filter(phase=phase.value) if engagement else Meeting.objects.none()
        summary.append({
            "phase": phase.value,
            "phase_display": phase.label,
            "is_current_phase": phase.value == current_phase,
            "counts": {
                "all": qs.count(),
                "upcoming": qs.filter(status__in=UPCOMING_BUCKET_STATUSES).count(),
                "completed": qs.filter(status=MeetingStatus.COMPLETED).count(),
                "cancelled": qs.filter(status=MeetingStatus.CANCELLED).count(),
            },
        })

    return Response(summary)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def create_meeting(request):
    """
    Staff only: create a meeting for a client's portal.
    Body: { "portal_id": 1, "title": "Initial Consultation", "scheduled_at": "2025-09-01T10:00:00Z", "phase": "align" }
    Optional "engagement_id": target a specific (possibly inactive) engagement
    instead of the portal's active one — lets staff pre-stage a future
    event's meetings before switching to it (see
    docs/FAILURE_POINTS_AUDIT.md F3 addendum).
    """
    portal_id = request.data.get("portal_id")
    if not portal_id:
        return _error("portal_id is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    portal = get_object_or_404(ClientPortal, id=portal_id)
    serializer = MeetingCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid meeting data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    engagement_id = request.data.get("engagement_id")
    if engagement_id:
        engagement = portal.engagements.filter(id=engagement_id).first()
        if not engagement:
            return _error("Engagement not found for this portal.", NOT_FOUND, status.HTTP_404_NOT_FOUND)
    else:
        engagement = portal.active_engagement
        if not engagement:
            return _error("This portal has no active engagement.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    meeting = save_with_attribution(serializer, request.user, engagement=engagement)
    return Response(MeetingDetailSerializer(meeting).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def meeting_detail(request, meeting_id):
    """
    GET:    staff or the portal owner — retrieve full meeting detail.
    PATCH:  staff only — partial update (title, scheduled_at, location, etc.).
    DELETE: staff only — permanently remove the meeting.
    """
    meeting = get_object_or_404(Meeting, id=meeting_id)

    if request.method == "GET":
        enforce(
            request.user.is_staff or request.user.is_superuser
            or (
                meeting.engagement is not None
                and meeting.engagement.portal.user == request.user
            ),
            "You do not have permission to view this meeting.",
        )
        return Response(MeetingDetailSerializer(meeting).data)

    enforce(
        request.user.is_staff or request.user.is_superuser,
        "Only staff can modify meetings.",
    )

    if request.method == "DELETE":
        meeting.delete()
        return Response({"detail": "Meeting deleted."})

    serializer = MeetingUpdateSerializer(meeting, data=request.data, partial=True)
    if not serializer.is_valid():
        return _error("Invalid meeting data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user)
    return Response(MeetingDetailSerializer(meeting).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def meeting_ics(request, meeting_id):
    """
    Download a single-VEVENT .ics file for this meeting (Outlook/Apple
    Calendar "Add to Calendar" — Google Calendar is a frontend-only deep
    link, no backend call needed there). Same read access as meeting_detail's
    GET: staff, or the meeting's own portal owner.
    """
    meeting = get_object_or_404(Meeting, id=meeting_id)
    enforce(
        request.user.is_staff or request.user.is_superuser
        or (
            meeting.engagement is not None
            and meeting.engagement.portal.user == request.user
        ),
        "You do not have permission to view this meeting.",
    )

    ics_content = services.build_ics(meeting)
    response = HttpResponse(ics_content, content_type="text/calendar; charset=utf-8")
    filename = slugify(meeting.title) or "meeting"
    response["Content-Disposition"] = f'attachment; filename="{filename}.ics"'
    return response


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def update_meeting_status(request, meeting_id):
    meeting = get_object_or_404(Meeting, id=meeting_id)
    new_status = request.data.get("status")
    if not new_status:
        return _error("status is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    try:
        meeting = services.transition_meeting_status(meeting, new_status)
    except ValidationError as e:
        return _error(str(e.detail[0]), INVALID_TRANSITION, status.HTTP_400_BAD_REQUEST)

    return Response(MeetingDetailSerializer(meeting).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def add_meeting_notes(request, meeting_id):
    """
    Staff only to write: add or replace this meeting's notes (summary, key
    discussions, key decisions, action items). These are client-visible —
    meeting_detail's GET is allowed for the portal owner too, and the Figma
    "Meeting Notes" page reads straight from this — staff authors them, but
    they are not internal-only.
    Only one notes record exists per meeting — this is an upsert.
    """
    meeting = get_object_or_404(Meeting, id=meeting_id)
    serializer = MeetingNotesSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid notes data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    MeetingNotes.objects.update_or_create(
        meeting=meeting,
        defaults=serializer.validated_data,
    )
    meeting.refresh_from_db()
    return Response(MeetingDetailSerializer(meeting).data, status=status.HTTP_201_CREATED)


# ── Prep items ────────────────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def add_prep_item(request, meeting_id):
    """
    Staff only: add a prep item (task group) to a meeting.
    Body: { "title": "Mood Board", "description": "Upload your inspiration images.", "fields": [...] }
    Also auto-sets meeting.preparation_required = true if not already set.
    """
    meeting = get_object_or_404(Meeting, id=meeting_id)
    serializer = MeetingPrepItemCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid prep item data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    # Every prep item must have at least one field (required or optional). A
    # field-less item has nothing for the client to do and — with completion now
    # derived purely from fields — could never be completed. Enforced here at
    # creation; delete_prep_field enforces the same floor from the other side.
    fields_data = request.data.get("fields", [])
    if not isinstance(fields_data, list):
        return _error("fields must be a list.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)
    if not fields_data:
        return _error("A prep item must have at least one field.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    # Validate every nested field BEFORE creating anything. The previous
    # version saved the prep item, then looped over `fields` saving whichever
    # ones happened to validate and silently dropping the rest — staff would
    # get a 201 with no indication that, say, field 2 of 3 was rejected for a
    # bad field_type and never actually got created.
    field_serializers = []
    for index, field_data in enumerate(fields_data):
        field_serializer = PrepItemFieldCreateSerializer(data=field_data)
        if not field_serializer.is_valid():
            return _error(
                f"Invalid data for fields[{index}].", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST,
                errors={f"fields[{index}]": field_serializer.errors},
            )
        field_serializers.append(field_serializer)

    with transaction.atomic():
        prep_item = save_with_attribution(serializer, request.user, meeting=meeting)
        for field_serializer in field_serializers:
            save_with_attribution(field_serializer, request.user, prep_item=prep_item)

        if not meeting.preparation_required:
            meeting.preparation_required = True
            meeting.save(update_fields=["preparation_required", "updated_at"])

        # An all-optional item is complete only once every (optional) field is
        # answered — which for a brand-new item is "not yet". A required-bearing
        # item likewise starts incomplete. Sync so is_completed reflects the
        # gate from the very first response rather than relying on the model
        # default happening to match.
        services.sync_prep_item_completion(prep_item)

    prep_item.refresh_from_db()
    return Response(MeetingPrepItemSerializer(prep_item).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def prep_item_detail(request, meeting_id, item_id):
    """
    GET    — read one prep item with its fields, responses and uploads.
             Staff, or the client who owns the meeting's engagement (they need
             it to render/refresh a single task group without re-fetching the
             whole meeting).
    PATCH  — staff only: edit title / description / order (not its fields;
             use the fields endpoints for those).
    DELETE — staff only: remove the prep item and everything under it (fields,
             responses, uploads) via cascade.
    """
    meeting = get_object_or_404(Meeting, id=meeting_id)
    # Reads are client-visible; writes stay staff-only. Checked here rather than
    # via a decorator because the two verbs need different gates.
    if request.method == "GET":
        enforce(
            _can_answer_prep(request.user, meeting),
            "You do not have permission to view this prep item.",
        )
    else:
        enforce(is_staff_or_superuser(request.user), "Only staff can modify prep items.")

    item = get_object_or_404(MeetingPrepItem, id=item_id, meeting=meeting)

    if request.method == "GET":
        return Response(MeetingPrepItemSerializer(item).data)

    if request.method == "DELETE":
        item.delete()
        return Response({"detail": "Prep item deleted."})

    serializer = MeetingPrepItemUpdateSerializer(item, data=request.data, partial=True)
    if not serializer.is_valid():
        return _error("Invalid prep item data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)
    save_with_attribution(serializer, request.user)
    return Response(MeetingPrepItemSerializer(item).data)


# ── Prep item fields ──────────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def add_prep_field(request, meeting_id, item_id):
    """
    Staff only: add an input field to an existing prep item.
    Body: { "field_type": "file_upload", "label": "Upload inspiration board", "is_required": true }
    Valid field_type values: qa | text | checkbox | file_upload
    """
    meeting = get_object_or_404(Meeting, id=meeting_id)
    item = get_object_or_404(MeetingPrepItem, id=item_id, meeting=meeting)
    serializer = PrepItemFieldCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid field data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    field = save_with_attribution(serializer, request.user, prep_item=item)
    # A fresh required field is unanswered by definition — if the item was
    # already marked complete (all fields satisfied at the time), it no
    # longer is, and the stored is_completed flag must reflect that.
    services.sync_prep_item_completion(item)
    item.refresh_from_db()
    return Response(PrepItemFieldSerializer(field).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def prep_field_detail(request, meeting_id, item_id, field_id):
    """
    Staff only.
    PATCH  — edit a field's label / helper_text / is_required / order / field_type.
             Changing field_type clears the client's existing answer for that
             field (a text answer is meaningless once it's a file field, and
             vice versa); the response carries a `warning` when that happens.
    DELETE — remove the field and any existing client response for it. Refused
             if it is the item's only field (a prep item must keep ≥1 field).
    """
    meeting = get_object_or_404(Meeting, id=meeting_id)
    item = get_object_or_404(MeetingPrepItem, id=item_id, meeting=meeting)
    field = get_object_or_404(PrepItemField, id=field_id, prep_item=item)

    if request.method == "DELETE":
        if item.fields.count() <= 1:
            return _error(
                "A prep item must keep at least one field. Delete the whole prep item instead.",
                VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST,
            )
        field.delete()
        # Removing the one still-unanswered field can make the item complete
        # again — re-evaluate rather than leaving a stale flag.
        services.sync_prep_item_completion(item)
        return Response({"detail": "Field removed."})

    serializer = PrepItemFieldUpdateSerializer(field, data=request.data, partial=True)
    if not serializer.is_valid():
        return _error("Invalid field data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    field, cleared_answer = services.update_prep_field(field, serializer.validated_data)
    body = PrepItemFieldSerializer(field).data
    if cleared_answer:
        body["warning"] = "Changing the field type cleared the client's existing answer for this field."
    return Response(body)


def _can_answer_prep(user, meeting) -> bool:
    """Staff, or the client who owns the meeting's engagement. Shared by the
    respond/upload endpoints — the only client-writable corner of meetings."""
    return (
        user.is_staff
        or user.is_superuser
        or (meeting.engagement is not None and meeting.engagement.portal.user == user)
    )


def _is_true(value) -> bool:
    """Truthy flag from JSON (bool) or multipart (the string 'true'/'1')."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


@api_view(["POST", "DELETE"])
@permission_classes([IsAuthenticated])
def respond_to_field(request, meeting_id, item_id, field_id):
    """
    Client or staff: submit, update, or clear a response to a prep item field.

    POST
      File fields:      multipart/form-data, key='files' (multiple allowed).
                        Files APPEND by default; send `replace=true` to swap the
                        whole set out (how you correct a wrong upload in one call).
      All other fields: JSON { "text_value": "..." } — re-POSTing overwrites.
      File uploads are also registered in the central Document hub automatically.

    DELETE
      Clears the answer entirely (the text response, or every uploaded file),
      putting the field back to unanswered.

    Both return the updated prep item so the frontend can reflect completion state.
    """
    meeting = get_object_or_404(Meeting, id=meeting_id)
    enforce(
        _can_answer_prep(request.user, meeting),
        "You do not have permission to respond to this item.",
    )
    item = get_object_or_404(MeetingPrepItem, id=item_id, meeting=meeting)
    field = get_object_or_404(PrepItemField, id=field_id, prep_item=item)

    if request.method == "DELETE":
        services.clear_field_response(field)
        item.refresh_from_db()
        return Response(MeetingPrepItemSerializer(item).data)

    files = request.FILES.getlist("files")
    # submit_field_response raises rest_framework.exceptions.ValidationError for
    # "field required" / "file required" — a genuine input-validation case, so
    # we let it propagate to custom_exception_handler (VALIDATION_ERROR envelope)
    # instead of duplicating that mapping here.
    services.submit_field_response(
        field, request.data, files=files, uploaded_by=request.user,
        replace=_is_true(request.data.get("replace", False)),
    )

    item.refresh_from_db()
    return Response(MeetingPrepItemSerializer(item).data)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def prep_upload_detail(request, meeting_id, item_id, field_id, upload_id):
    """
    Client or staff: remove ONE uploaded file from a file field, leaving the
    rest in place — the granular counterpart to `replace=true` on respond.
    Returns the updated prep item so completion state stays in sync.
    """
    meeting = get_object_or_404(Meeting, id=meeting_id)
    enforce(
        _can_answer_prep(request.user, meeting),
        "You do not have permission to modify this upload.",
    )
    item = get_object_or_404(MeetingPrepItem, id=item_id, meeting=meeting)
    field = get_object_or_404(PrepItemField, id=field_id, prep_item=item)
    upload = get_object_or_404(PrepItemFileUpload, id=upload_id, field=field)

    services.delete_prep_upload(upload)
    item.refresh_from_db()
    return Response(MeetingPrepItemSerializer(item).data)
