from django.db import models
from django.db.models import F, Q

from apps.core.models import AttributedModel, UUIDTimestampedModel
from apps.events.models import Event

# Create your models here.

class InquiryForm(UUIDTimestampedModel, AttributedModel):
    """
    A prospective client's submitted lead.

    **Attribution.** `created_by` is always NULL, and that is correct rather than
    a gap: the submit route is public and unauthenticated, so no staff member
    creates a lead. The meaningful pair is `last_updated_by` / `updated_at`.

    `status` is the ONLY mutable field on this model — `InquirySerializer` is
    entirely read-only and the sole write route is the status PATCH — so the
    generic attribution pair *is* the status attribution
    INQUIRY_V2_BACKLOG.md §2 asked for. That is why there is no bespoke
    `status_updated_by` here the way `EventEngagement` carries
    `phase_updated_by`: a portal has many mutable fields and must single one out,
    an inquiry has exactly one. **If a second mutable field is ever added**
    (backlog §3, `assigned_to`), that reasoning expires — `last_updated_by`
    degrades to "who last touched it" and a status-specific pair is needed.

    Note the admin can still edit every field, and admin saves do not run through
    `save_with_attribution`, so attribution covers the API surface only.
    """

    # event_type shares ONE vocabulary with events.Event — a lead that converts
    # maps onto a real event without a translation table. Do not re-declare the
    # list here; add new types on Event.EVENT_TYPE.
    CONTACT_MODE = [
        ("Email", "Email"),
        ("Phone Number", "Phone Number"),
    ]

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        QUALIFIED = "qualified", "Qualified"
        CONVERTED = "converted", "Converted"
        LOST = "lost", "Lost"
        ARCHIVED = "archived", "Archived"

    first_name = models.CharField( max_length=255)
    last_name = models.CharField( max_length=255)
    email = models.EmailField(max_length=255)
    phone_number = models.CharField(max_length=20)
    contact_mode = models.CharField(max_length=12, choices=CONTACT_MODE, blank = True, null=True)
    event_type = models.CharField(max_length=20, choices=Event.EVENT_TYPE, blank = True, null=True)
    preferred_start_date = models.DateField(null=True)
    preferred_end_date = models.DateField(null=True)
    desired_location = models.CharField(max_length=255)
    budget = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    details = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(preferred_end_date__gte=F('preferred_start_date')),
                name='valid_preferred_date_range'
            )
        ]

    def __str__(self):
        event = self.event_type if self.event_type else "Event Inquiry"
        return f"{self.first_name} {self.last_name} - {event} @ {self.desired_location}"
