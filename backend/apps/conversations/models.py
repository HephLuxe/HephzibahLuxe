import uuid

from django.db import models

from apps.core.models import AttributedModel
from apps.portal.models import PlanningPhase


class ContactMethod(models.TextChoices):
    WHATSAPP = "whatsapp", "WhatsApp"
    PHONE = "phone", "Phone"
    EMAIL = "email", "Email"


class ConversationTag(models.TextChoices):
    """
    The "Filter by" checkbox list on Questions & Clarifications. Also the
    vocabulary for the deep-link pills shown on each conversation card
    (e.g. a conversation tagged EVENT_DETAILS links back to the Event Details
    page) — one tag list serves both purposes, not two separate ones.
    """
    ASO_EBI = "aso_ebi", "Aso-Ebi"
    CATERING = "catering", "Catering"
    CREATIVE_DESIGN = "creative_design", "Creative Design"
    DECOR_DESIGN = "decor_design", "Decor & Design"
    ENTERTAINMENT = "entertainment", "Entertainment"
    EVENT_DETAILS = "event_details", "Event Details"
    BUDGET = "budget", "Budget"
    SOUVENIR = "souvenir", "Souvenir"
    VENUE = "venue", "Venue"


class Conversation(AttributedModel):
    # UUID PK — exposed directly in URLs (/conversations/<id>/); a sequential
    # int PK there is enumerable.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    engagement = models.ForeignKey("portal.EventEngagement", on_delete=models.CASCADE, related_name="conversations", null=True, blank=True)
    phase = models.CharField(max_length=20, choices=PlanningPhase.choices)
    conversation_with = models.CharField(max_length=255)
    contact_method = models.CharField(max_length=20, choices=ContactMethod.choices, default=ContactMethod.WHATSAPP)
    title = models.TextField()
    body = models.TextField()
    tags = models.JSONField(default=list, blank=True)
    links = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.conversation_with} — {self.title[:50]}"

