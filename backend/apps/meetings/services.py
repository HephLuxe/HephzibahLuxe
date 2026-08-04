import datetime

from django.contrib.auth.base_user import AbstractBaseUser
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from rest_framework.exceptions import ValidationError
from .models import (
    Meeting,
    MeetingPrepItem,
    MeetingStatus,
    FieldType,
    PrepItemField,
    PrepItemResponse,
    PrepItemFileUpload,
)
from apps.documents.services import register_document
from apps.documents.models import DocumentCategory

ALLOWED_PREP_UPLOAD_TYPES = ["application/pdf", "image/jpeg", "image/png", "image/webp"]
MAX_PREP_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB — inspiration boards/photos, not raw video


VALID_TRANSITIONS = {
    MeetingStatus.UPCOMING: [
        MeetingStatus.ACTIVE,
        MeetingStatus.CANCELLED,
        MeetingStatus.RESCHEDULED,
    ],
    MeetingStatus.ACTIVE: [
        MeetingStatus.COMPLETED,
        MeetingStatus.CANCELLED,
        MeetingStatus.RESCHEDULED,
    ],
    MeetingStatus.RESCHEDULED: [
        MeetingStatus.UPCOMING,
        MeetingStatus.CANCELLED,
    ],
    MeetingStatus.COMPLETED: [],
    MeetingStatus.CANCELLED: [],
}


def transition_meeting_status(meeting: Meeting, new_status: str) -> Meeting:
    allowed = VALID_TRANSITIONS.get(meeting.status, [])
    if new_status not in allowed:
        raise ValidationError(
            f"Cannot transition from '{meeting.status}' to '{new_status}'."
        )
    meeting.status = new_status
    meeting.save(update_fields=["status", "updated_at"])
    return meeting


def _validate_prep_upload(file: UploadedFile) -> None:
    content_type = getattr(file, "content_type", None)
    if content_type and content_type not in ALLOWED_PREP_UPLOAD_TYPES:
        raise ValidationError(
            f"'{file.name}' must be a PDF or image (JPEG, PNG, WebP)."
        )
    if file.size > MAX_PREP_UPLOAD_SIZE:
        raise ValidationError(f"'{file.name}' exceeds the 10MB upload limit.")


def submit_field_response(
    field: PrepItemField,
    data: dict,
    files: list[UploadedFile] | None = None,
    uploaded_by: AbstractBaseUser | None = None,
    replace: bool = False,
) -> PrepItemResponse | list[PrepItemFileUpload]:
    """
    Submit or update an answer to a prep field.

    Text/QA/checkbox fields are update_or_create — re-submitting simply
    overwrites the previous answer.

    File fields APPEND by default (a client adding a second inspiration image
    shouldn't wipe the first). Pass `replace=True` to swap the whole set out
    instead — that's how a client corrects a wrong upload without needing to
    delete each old file first. Deleting the superseded rows fires the
    post_delete signal in signals.py, which unregisters each Document row and
    removes its blob, so a replace leaves nothing dangling.
    """

    if field.field_type == FieldType.FILE_UPLOAD:
        if not files:
            raise ValidationError("At least one file is required for this field.")

        for file in files:
            _validate_prep_upload(file)

        if replace:
            # Validated above, so we only drop the old set once the new one is
            # known-good — a rejected upload never destroys the existing answer.
            for old in list(field.uploads.all()):
                old.delete()

        uploads = []
        for file in files:
            upload = PrepItemFileUpload.objects.create(
                field=field,
                file=file,
                filename=file.name,
            )
            register_document(
                engagement=field.prep_item.meeting.engagement,
                source_instance=upload,
                file_path=upload.file.name,
                category=DocumentCategory.PREP_UPLOAD,
                uploaded_by=uploaded_by,
                file_size=file.size,
                mime_type=getattr(file, "content_type", ""),
            )
            uploads.append(upload)

        sync_prep_item_completion(field.prep_item)
        return uploads

    elif field.field_type == FieldType.CHECKBOX:
        text = data.get("text_value", "").strip()
        if field.is_required and not text:
            raise ValidationError("This field is required.")
        response, _ = PrepItemResponse.objects.update_or_create(
            field=field,
            defaults={"text_value": text},
        )
        sync_prep_item_completion(field.prep_item)
        return response

    else:  # qa, text
        text = data.get("text_value", "").strip()
        if field.is_required and not text:
            raise ValidationError("This field is required.")
        response, _ = PrepItemResponse.objects.update_or_create(
            field=field,
            defaults={"text_value": text},
        )
        sync_prep_item_completion(field.prep_item)
        return response


def delete_prep_upload(upload: PrepItemFileUpload) -> PrepItemField:
    """
    Remove one uploaded file from a file field, then re-derive the prep item's
    completion (dropping the last file on a required field makes it unanswered
    again). The post_delete signal handles the Document registry row + blob.
    Returns the field the upload belonged to.
    """
    field = upload.field
    upload.delete()
    sync_prep_item_completion(field.prep_item)
    return field


def clear_field_response(field: PrepItemField) -> PrepItemField:
    """
    Wipe a client's answer to a field — the text response, or every uploaded
    file — putting it back to unanswered. Used by the DELETE half of the
    respond endpoint so a client can retract an answer, not just overwrite it.
    """
    if field.field_type == FieldType.FILE_UPLOAD:
        for upload in list(field.uploads.all()):
            upload.delete()
    else:
        PrepItemResponse.objects.filter(field=field).delete()
    sync_prep_item_completion(field.prep_item)
    return field


def field_is_answered(field: PrepItemField) -> bool:
    """
    Whether a single field has been satisfied by the client — its own
    completion state, tracked identically for required and optional fields:

      * file_upload — at least one file uploaded
      * checkbox    — ticked ("true"); an unticked/false box is not answered
      * qa / text   — a non-empty response

    Used both to recompute an item's is_completed (sync_prep_item_completion)
    and to render each field's is_completed / the answered counts in the API.
    """
    if field.field_type == FieldType.FILE_UPLOAD:
        return field.uploads.exists()

    response = getattr(field, "response", None)
    if field.field_type == FieldType.CHECKBOX:
        return response is not None and response.text_value == "true"
    # qa, text
    return response is not None and bool(response.text_value.strip())


def sync_prep_item_completion(prep_item: MeetingPrepItem) -> None:
    """
    Recompute is_completed from the CURRENT state of the item's fields, letting
    it move in either direction (answering completes it; clearing an answer, or
    staff adding a fresh required field, un-completes it).

    The gate:
      * item has ≥1 required field  → complete ⇔ every REQUIRED field answered
                                       (optional fields never block it)
      * item has only optional fields → complete ⇔ every field answered
                                       (nothing is required, so the whole
                                       checklist must be filled — an all-optional
                                       item behaves like an onboarding checklist,
                                       not an instantly-satisfied one)
      * item has no fields at all    → not complete (shouldn't occur — add_prep_item
                                       requires ≥1 field — but handled defensively)

    Called after any response/upload change (submit_field_response), on item
    creation (add_prep_item), and after staff add/remove/edit a field
    (add_prep_field / delete_prep_field / update_prep_field).
    """
    fields = list(prep_item.fields.all())
    required = [f for f in fields if f.is_required]
    gate = required if required else fields  # required fields, else the whole checklist

    complete = bool(gate) and all(field_is_answered(f) for f in gate)

    if complete != prep_item.is_completed:
        prep_item.is_completed = complete
        prep_item.save(update_fields=["is_completed"])


def update_prep_field(field: PrepItemField, validated_data: dict) -> tuple[PrepItemField, bool]:
    """
    Staff edit of a single field. Returns (field, cleared_answer).

    Changing field_type invalidates any answer already given — a text response
    is meaningless once the field is a file upload, and vice versa — so the
    existing PrepItemResponse and any uploaded files for this field are cleared
    when (and only when) the type actually changes. `cleared_answer` is True in
    that case, so the view can warn the client. Editing only the label /
    helper_text / is_required / order leaves answers untouched.
    """
    old_type = field.field_type
    for attr, value in validated_data.items():
        setattr(field, attr, value)
    field.save()

    cleared_answer = False
    if field.field_type != old_type:
        deleted_response, _ = PrepItemResponse.objects.filter(field=field).delete()
        deleted_uploads = field.uploads.count()
        field.uploads.all().delete()
        cleared_answer = bool(deleted_response or deleted_uploads)

    # is_required may have flipped and/or the answer may have been wiped — either
    # can change whether the item's gate is satisfied, so always re-sync.
    sync_prep_item_completion(field.prep_item)
    return field, cleared_answer


# ── Calendar export ──────────────────────────────────────────────

def _escape_ics_text(value: str) -> str:
    """RFC 5545 §3.3.11 escaping for TEXT values: backslash, comma, semicolon,
    and newlines must be escaped, in that order (escaping the backslash first
    would double-escape the ones inserted for the other characters)."""
    return (
        value.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("\n", "\\n")
    )


def _ics_datetime(date: datetime.date, time: datetime.time) -> str:
    """
    Format a naive date+time as an RFC 5545 UTC datetime (YYYYMMDDTHHMMSSZ).
    No timezone conversion is needed: config/settings.py has TIME_ZONE='UTC'
    with USE_TZ=True, so every Meeting.date/time already represents a UTC
    wall-clock moment.
    """
    return datetime.datetime.combine(date, time).strftime("%Y%m%dT%H%M%SZ")


def build_ics(meeting: Meeting) -> bytes:
    """
    A minimal single-VEVENT .ics file for the "Add to Calendar" download —
    covers Outlook/Apple Calendar, which (unlike Google Calendar) need a real
    file rather than a deep-link URL. Built with the stdlib only; a single
    VEVENT doesn't warrant the `icalendar` package as a new dependency.
    """
    dtstart = _ics_datetime(meeting.date, meeting.time)
    end_dt = datetime.datetime.combine(meeting.date, meeting.time) + datetime.timedelta(
        minutes=meeting.duration_minutes
    )
    dtend = end_dt.strftime("%Y%m%dT%H%M%SZ")
    dtstamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
    ics_status = "CANCELLED" if meeting.status == MeetingStatus.CANCELLED else "CONFIRMED"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Hephzibah Luxe//Client Portal//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{meeting.id}@hephluxe.com",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        f"SUMMARY:{_escape_ics_text(meeting.title)}",
        f"STATUS:{ics_status}",
    ]
    if meeting.description:
        lines.append(f"DESCRIPTION:{_escape_ics_text(meeting.description)}")
    if meeting.meeting_url:
        lines.append(f"URL:{meeting.meeting_url}")
        lines.append(f"LOCATION:{_escape_ics_text(meeting.meeting_url)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]

    # RFC 5545 §3.1 requires CRLF line endings.
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")
