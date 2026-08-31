import uuid

from django.db import models

from apps.core.models import AttributedModel
from apps.core.utils import prep_upload_path
from apps.portal.models import PlanningPhase


class MeetingStatus(models.TextChoices):
    UPCOMING = "upcoming", "Upcoming"
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    RESCHEDULED = "rescheduled", "Rescheduled"


class Meeting(AttributedModel):
    # UUID PK — exposed directly in URLs (/meetings/<id>/, and nested prep/
    # field routes below it); a sequential int PK there is enumerable.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    engagement = models.ForeignKey("portal.EventEngagement", on_delete=models.CASCADE, related_name="meetings", null=True, blank=True)
    title = models.CharField(max_length=255)
    date = models.DateField()
    time = models.TimeField()
    duration_minutes = models.IntegerField(default=60)
    meeting_url = models.URLField(blank=True)
    status = models.CharField(max_length=20, choices=MeetingStatus.choices, default=MeetingStatus.UPCOMING)
    phase = models.CharField(max_length=20, choices=PlanningPhase.choices)
    description = models.TextField(blank=True)
    preparation_required = models.BooleanField(default=False)
    # Set by meetings.tasks.meeting_prep_digest_task the first time it emails
    # about outstanding prep for this meeting — avoids re-sending daily while
    # the meeting is still upcoming and prep is still incomplete. Reset to
    # None by services.sync_prep_item_completion-adjacent flows is NOT done
    # automatically — a fresh required field added after the digest already
    # fired will wait for the next distinct meeting to get its own digest,
    # which is an acceptable, deliberately simple tradeoff (see notifications
    # app README).
    prep_reminder_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "time"]

    def __str__(self):
        if self.engagement:
            return f"{self.title} — {self.engagement.portal.user}"
        return f"{self.title} — (no engagement)"


class MeetingPrepItem(AttributedModel):
    # UUID PK — exposed in URLs (/meetings/<id>/prep/<item_id>/...).
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="prep_items")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_completed = models.BooleanField(default=False)
    order = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.title} — {self.meeting.title}"


class FieldType(models.TextChoices):
    CHECKBOX = "checkbox", "Checkbox"
    FILE_UPLOAD = "file_upload", "File Upload"
    QA = "qa", "Question & Answer"
    TEXT = "text", "Text Input"


class PrepItemField(AttributedModel):
    """
    Admin defines what fields a prep item requires.
    e.g. a single prep item can have:
      - a Q&A field ("What is your vision for the ceremony?")
      - a file upload field ("Upload your inspiration board")
      - a checkbox ("I confirm the above details are correct")
    """
    # UUID PK — exposed in URLs (/meetings/<id>/prep/<item_id>/fields/<field_id>/...).
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    prep_item = models.ForeignKey(MeetingPrepItem, on_delete=models.CASCADE, related_name="fields")
    field_type = models.CharField(max_length=20, choices=FieldType.choices)
    label = models.CharField(max_length=255)      # "Upload your inspiration board"
    helper_text = models.TextField(blank=True)    # extra instruction shown to client
    is_required = models.BooleanField(default=True)
    order = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.label} ({self.get_field_type_display()}) — {self.prep_item.title}"


class PrepItemResponse(models.Model):
    """
    Client's response to a single non-file field (checkbox, QA, text).
    """
    field = models.OneToOneField(PrepItemField, on_delete=models.CASCADE, related_name="response")
    text_value = models.TextField(blank=True)           # qa, text — NOT checkbox (see bool_value)
    # Checkbox answers only. Previously a checkbox stored the STRING "true"/"false"
    # in text_value, which meant the column accepted any prose at all: the write
    # path was a byte-for-byte copy of the qa/text branch, and the read path
    # compared `text_value == "true"` exactly. A client could submit a paragraph
    # to a required checkbox, get a 200, and leave the field permanently
    # unanswerable. Nullable because it is meaningless for the other three field
    # types, which leave it NULL.
    bool_value = models.BooleanField(null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Response to '{self.field.label}'"
    
    def get_portal_id(self):
        return self.field.prep_item.meeting.engagement.portal.id


class PrepItemFileUpload(models.Model):
    """Multiple files for a single file_upload field."""
    field = models.ForeignKey(
        PrepItemField, on_delete=models.CASCADE, related_name="uploads"
    )
    file = models.FileField(upload_to=prep_upload_path, max_length=255)
    filename = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def get_portal_id(self):
        return self.field.prep_item.meeting.engagement.portal.id


class MeetingNotes(AttributedModel):
    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE, related_name="notes")
    summary = models.TextField()
    key_discussions = models.JSONField(default=list)
    key_decisions = models.JSONField(default=list)
    action_items = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Meeting Notes"
        verbose_name_plural = "Meeting Notes"

    def __str__(self):
        return f"Notes — {self.meeting.title}"