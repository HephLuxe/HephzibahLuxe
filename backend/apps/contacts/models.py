import uuid

from django.db import models

from apps.core.models import AttributedModel
from apps.core.utils import contact_photo_upload_path


class ContactCategory(models.TextChoices):
    PRIMARY = "primary", "Primary Contacts"
    DECISION_MAKER = "decision_maker", "Decision Makers & Approvals"
    FAMILY_VIP = "family_vip", "Family & VIP Representatives"
    KEY_PARTICIPANT = "key_participant", "Key Participants"
    EVENT_DAY = "event_day", "Event-Day Contacts"


class PreferredMethod(models.TextChoices):
    WHATSAPP = "whatsapp", "WhatsApp"
    EMAIL = "email", "Email"
    PHONE = "phone", "Phone"


class EventContact(AttributedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="contacts")
    event_day = models.ForeignKey("events.EventDay", on_delete=models.CASCADE,related_name="day_contacts")
    category = models.CharField(max_length=20, choices=ContactCategory.choices)
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=255, blank=True)          # e.g. "Client - Bride"
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    preferred_method = models.CharField(max_length=20, choices=PreferredMethod.choices, blank=True)
    # No `storage=` argument, so this takes the DEFAULT (private, signed) tier.
    # It used to be on the public bucket, which made every contact photo a
    # permanent, unauthenticated URL — and contacts are the client's family,
    # bridal party and vendors, not portfolio subjects. Read it through
    # `GET /files/contact-photo/<id>/`, which checks portal ownership and mints
    # a 60-second URL (apps/core/filelinks.py).
    photo = models.ImageField(upload_to=contact_photo_upload_path, blank=True, max_length=500)
    # created_by / last_updated_by come from AttributedModel (apps/core/models.py).
    # FKs, not name strings (see docs/FAILURE_POINTS_AUDIT.md F11) — a frozen
    # text snapshot goes stale on rename, can't disambiguate two staff with
    # the same name, and can't be queried by account.
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "event_day", "category", "email"],
                name="unique_contact_per_category_per_day",
                condition=models.Q(email__gt=""),  # only enforce when email is provided
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.get_category_display()}) — {self.event.title}"

    def get_portal_id(self):
        try:
            return self.event.celebrant.portal.id
        except AttributeError:
            raise ValueError(
                f"EventContact '{self.name}' event has no celebrant or the celebrant has no portal. "
                "Cannot generate upload path."
            )