from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from apps.portal.models import ClientPortal


class DocumentCategory(models.TextChoices):
    EVENT_COVER     = "event_cover",    "Event Cover Image"
    EVENT_IMAGE     = "event_image",    "Event Day Image"
    PREP_UPLOAD     = "prep_upload",    "Prep Item Upload"
    CONTACT_PHOTO   = "contact_photo",  "Contact Photo"
    TEAM_PHOTO      = "team_photo",     "Team Member Photo"
    # INVOICE         = "invoice",        "Invoice"
    # CONTRACT        = "contract",       "Contract"
    # OTHER           = "other",          "Other"


class Document(models.Model):
    engagement = models.ForeignKey("portal.EventEngagement", on_delete=models.CASCADE, related_name="documents", null=True, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,null=True, blank=True)

    # Generic FK — links back to whatever model produced this file
    # (PrepItemResponse, Event, EventDay, EventContact, etc.)
    # CharField, not PositiveIntegerField: source models have mixed PK types
    # (Event/PrepItemFileUpload use plain int PKs; EventDay/EventContact/
    # TeamMember use UUID PKs) — see docs/FAILURE_POINTS_AUDIT.md F4. A
    # CharField holds either representation as a string; Django stringifies
    # whatever is assigned (CharField.get_prep_value), so register_document's
    # `object_id=source_instance.pk` needs no change either way.
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    object_id = models.CharField(max_length=255, null=True, blank=True)
    source = GenericForeignKey("content_type", "object_id")

    category    = models.CharField(max_length=30, choices=DocumentCategory.choices)
    file_path = models.CharField(max_length=500, null=True, blank=True)  # relative path, e.g. "portals/.../cover.jpg"
    filename    = models.CharField(max_length=255)
    file_size   = models.PositiveIntegerField(null=True, blank=True)  # bytes
    mime_type   = models.CharField(max_length=100, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        portal_user = self.engagement.portal.user if self.engagement else "no portal"
        return f"{self.filename} — {portal_user}"