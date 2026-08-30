import os
import uuid

from django.conf import settings
from django.db import models

from apps.core.models import AttributedModel


# Create your models here.
class PlanningPhase(models.TextChoices):
    CONNECT = "connect", "No. 01: Connect"
    ALIGN = "align", "No. 02: Align"
    CURATE = "curate", "No. 03: Curate"
    ENVISION = "envision", "No. 04: Envision"
    ORCHESTRATE = "orchestrate", "No. 05: Orchestrate"
    DELIVER = "deliver", "No. 06: Deliver"

class ClientPortal(AttributedModel):
    """One per client — their portal configuration."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="portal")
    welcome_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def active_engagement(self):
        return self.engagements.filter(is_active=True).first()

    @property
    def active_event(self):
        eng = self.active_engagement
        return eng.event if eng else None

    @property
    def current_phase(self):
        eng = self.active_engagement
        return eng.current_phase if eng else None

    def __str__(self):
        return f"Portal — {self.user}"


def team_photo_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1]
    # pk may be None on first save — Django will re-call this after insert if needed
    member_id = instance.pk or "new"
    return f"team_photos/{member_id}/photo{ext}"

class TeamMember(AttributedModel):
    """Staff profiles shown on client portals (e.g. Winnie, Tosin)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=255)          # "Client Experience Lead"
    bio = models.TextField(blank=True)
    photo = models.ImageField(upload_to=team_photo_upload_path, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    # Members flagged here are auto-assigned to every NEW client portal (the
    # "Meet Your Team" defaults) — see portal.services.seed_default_team_members,
    # fired by the ClientPortal post_save signal.
    is_default = models.BooleanField(
        default=False, help_text="Auto-assigned to every new client portal.",
    )

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        ordering = ["name"]
 
    def __str__(self):
        return f"{self.name} ({self.role})"

class PortalTeamAssignment(AttributedModel):
    """Which team members are assigned to which portal."""
    portal = models.ForeignKey(ClientPortal, on_delete=models.CASCADE, related_name="team_assignments")
    team_member = models.ForeignKey(TeamMember, on_delete=models.CASCADE)

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        unique_together = ("portal", "team_member")
 
    def __str__(self):
        return f"{self.team_member} → {self.portal}"

class EventEngagement(AttributedModel):
    """
    Bridges one client portal to one event.
    Replaces active_event + current_phase + phase_details + contacts_locked on ClientPortal.
    One portal can have many engagements (one per event), but only one is active.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    portal = models.ForeignKey(ClientPortal, on_delete=models.CASCADE, related_name="engagements")
    event = models.OneToOneField("events.Event", on_delete=models.CASCADE, related_name="engagement")
    is_active = models.BooleanField(default=True)
    current_phase = models.CharField(max_length=20, choices=PlanningPhase.choices, default=PlanningPhase.CONNECT)
    phase_details = models.JSONField(default=dict, blank=True)
    contacts_locked = models.BooleanField(default=False)
    # Gates client edits to Event/EventDay (update_event, update_eventday) —
    # deliberately a separate flag from contacts_locked: staff may want to
    # freeze event details (date, venue, ...) independently of the contact
    # list, and vice versa. Deletion is unaffected by this flag — clients can
    # never delete an event/event day regardless of lock state (see events/views.py).
    event_details_locked = models.BooleanField(default=False)
    # The middle segment of every Document Hub reference code for this
    # engagement (e.g. "PSW006" in HL-PSW006-INV001). Encodes the couple/honoree
    # initials + event-type letter + a global per-event-type count — see
    # document_hub.services.assign_engagement_segment. System-assigned on first
    # need and unique across engagements. NULL until assigned lazily the first
    # time a code is generated for the engagement.
    reference_segment = models.CharField(max_length=20, unique=True, null=True, blank=True)
    # Who last changed the planning phase, and when — drives the "Last Updated
    # by …" attribution on the Planning Stage. Separate from updated_at (which
    # moves on any save, e.g. a lock toggle).
    phase_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="phase_updates",
    )
    phase_updated_at = models.DateTimeField(null=True, blank=True)
    # ── Debounce state for the "event details updated" client email ──
    #
    # Every edit to this engagement's Event or any of its EventDays re-stamps all
    # three fields (apps.events.services.schedule_event_details_notification), so
    # a burst of edits collapses into exactly one email describing the most
    # recent change, sent once the editor has been quiet for the full window.
    # apps.events.tasks.dispatch_due_event_details_notifications is the sweep
    # that picks up whatever is due.
    #
    # All three are columns on purpose. This used to be one column plus a Celery
    # `apply_async(countdown=900)`: the token was durable, but "and send it at
    # T+900s" lived only in the broker's ETA queue, and `what` lived only as a
    # task argument. Half the state in Postgres and half in a message meant a
    # worker restart or a deploy inside the window could drop the email entirely
    # — recovery depended on `visibility_timeout` redelivery, an implementation
    # detail rather than a guarantee. Now the whole schedule is a row, so the
    # next sweep sends it no matter what died in between.
    #
    # The token is now an audit marker rather than the supersession mechanism —
    # `due_at` carries that, since a later edit simply pushes it further out and
    # the sweep claims a row by nulling it. Kept because "which pending
    # notification is this?" is a question worth being able to answer in the
    # admin and in logs, and it is what the old task's arguments were keyed on.
    event_details_notify_token = models.UUIDField(null=True, blank=True)
    # When the debounce window closes. NULL = nothing pending. Indexed because
    # the sweep's only query is "everything due at or before now".
    event_details_notify_due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # Human description of the most recent change ("the event date", "a contact",
    # ...), rendered in the email body. Travelled as a task argument before there
    # was anywhere durable to put it.
    event_details_notify_what = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["portal"],
                condition=models.Q(is_active=True),
                name="unique_active_engagement_per_portal",
            )
        ]

    def __str__(self):
        status = "active" if self.is_active else "past"
        return f"{self.portal.user} → {self.event.title} ({status})"


class PortalSettings(models.Model):
    """
    System-wide portal behaviour, configured once in the Django admin (no API).
    Singleton: pinned to one fixed PK so save() can only ever write one row.

    Auto-lock: when enabled, an engagement's event details and/or contacts are
    locked automatically once its planning phase reaches `auto_lock_phase` (or
    any later phase). Lock-on-reach only — it never auto-unlocks; staff can still
    unlock manually. See portal.services._apply_auto_lock.

    Event-details notification debounce: how long an "event details updated"
    client email waits after the LAST edit before sending — see
    apps.events.services.schedule_event_details_notification. Lives here
    (rather than a Django setting) so staff can change it without a redeploy,
    the same reasoning as the auto-lock fields above.

    Contact info: the email/WhatsApp number shown on the client portal's
    "Contact Your Team" (see portal.serializers.PortalOverviewSerializer.get_contact).
    Used to live as settings.HEPHZIBAH_CONTACT (env-sourced) — moved here for
    the same reason as everything else on this model: changing a phone number
    or support inbox shouldn't require a redeploy.
    """
    SINGLETON_ID = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_ID, editable=False)
    auto_lock_enabled = models.BooleanField(
        default=False, help_text="Master switch for phase-based auto-locking.",
    )
    auto_lock_phase = models.CharField(
        max_length=20, choices=PlanningPhase.choices, default=PlanningPhase.ORCHESTRATE,
        help_text="Locking kicks in when the engagement reaches this phase (or later).",
    )
    auto_lock_event_details = models.BooleanField(
        default=True, help_text="Auto-lock event/event-day editing.",
    )
    auto_lock_contacts = models.BooleanField(
        default=True, help_text="Auto-lock contact editing.",
    )
    event_details_notify_debounce_seconds = models.PositiveIntegerField(
        default=900,
        help_text=(
            "Seconds of inactivity on an event (or its event days) before the "
            "'event details updated' client email sends. Every edit resets this "
            "window, so a burst of edits collapses into one email. Default 900 = 15 min."
        ),
    )
    contact_email = models.EmailField(
        blank=True,
        help_text="Shown on the client portal's 'Contact Your Team' — Send an Email.",
    )
    contact_whatsapp = models.CharField(
        max_length=32, blank=True,
        help_text=(
            "WhatsApp number for 'Contact Your Team' — Send a Message. Include the "
            "country code (e.g. +2348023203870); this is rendered into a wa.me link."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Portal Settings"
        verbose_name_plural = "Portal Settings"

    def __str__(self):
        return "Portal Settings"

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_ID
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "PortalSettings":
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_ID)
        return obj