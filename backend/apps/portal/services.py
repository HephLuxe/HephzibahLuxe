from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.utils import stamp_attribution

from .models import (
    ClientPortal,
    EventEngagement,
    PlanningPhase,
    PortalSettings,
    PortalTeamAssignment,
    TeamMember,
)

# Ordered phase progression
PHASE_ORDER = [
    PlanningPhase.CONNECT,
    PlanningPhase.ALIGN,
    PlanningPhase.CURATE,
    PlanningPhase.ENVISION,
    PlanningPhase.ORCHESTRATE,
    PlanningPhase.DELIVER,
]


def advance_phase(portal: ClientPortal, user=None) -> EventEngagement:
    """
    Move the active engagement to the next planning phase.
    Raises ValidationError if no active engagement or already at final phase.
    Staff-only — caller must enforce permission before calling. `user` is
    recorded as who changed the phase (attribution).
    """
    engagement = portal.active_engagement
    if not engagement:
        raise ValidationError("No active engagement found for this portal.")

    current_index = PHASE_ORDER.index(engagement.current_phase)
    if current_index >= len(PHASE_ORDER) - 1:
        raise ValidationError("Portal is already at the final phase (Deliver).")

    engagement.current_phase = PHASE_ORDER[current_index + 1]
    _apply_phase_change(engagement, user)
    return engagement


def set_phase(portal: ClientPortal, new_phase: str, user=None) -> EventEngagement:
    """
    Explicitly set the active engagement to any valid planning phase.
    Staff-only — caller must enforce permission before calling.
    """
    if new_phase not in PlanningPhase.values:
        raise ValidationError(f"'{new_phase}' is not a valid planning phase.")

    engagement = portal.active_engagement
    if not engagement:
        raise ValidationError("No active engagement found for this portal.")

    engagement.current_phase = new_phase
    _apply_phase_change(engagement, user)
    return engagement


def _apply_phase_change(engagement: EventEngagement, user) -> None:
    """
    Shared tail of advance_phase/set_phase: stamp attribution, apply phase-based
    auto-lock, persist in one save, then notify the client. `engagement`
    already has its new current_phase set.
    """
    engagement.phase_updated_by = user
    engagement.phase_updated_at = timezone.now()
    update_fields = ["current_phase", "phase_updated_by", "phase_updated_at", "updated_at"]
    # Generic attribution too, alongside the phase-specific pair above.
    if stamp_attribution(engagement, user, creating=False):
        update_fields.append("last_updated_by")
    _apply_auto_lock(engagement, update_fields)
    engagement.save(update_fields=update_fields)
    _notify_phase_changed(engagement)


def _apply_auto_lock(engagement: EventEngagement, update_fields: list[str]) -> None:
    """
    Lock event details and/or contacts once the engagement reaches the
    configured phase (or later), if auto-locking is enabled (PortalSettings,
    admin-managed). Lock-on-reach only — never auto-unlocks; staff can still
    unlock manually. Mutates `engagement` and appends changed field names.
    """
    config = PortalSettings.load()
    if not config.auto_lock_enabled:
        return
    if PHASE_ORDER.index(engagement.current_phase) < PHASE_ORDER.index(config.auto_lock_phase):
        return
    if config.auto_lock_event_details and not engagement.event_details_locked:
        engagement.event_details_locked = True
        update_fields.append("event_details_locked")
    if config.auto_lock_contacts and not engagement.contacts_locked:
        engagement.contacts_locked = True
        update_fields.append("contacts_locked")


def _notify_phase_changed(engagement: EventEngagement) -> None:
    """Email the client that their planning phase moved (gated by the global
    notifications switch)."""
    from apps.notifications.services import queue_notification

    portal = engagement.portal
    queue_notification(
        recipient_email=portal.user.email,
        recipient_user=portal.user,
        engagement=engagement,
        template_name="phase_advanced",
        context={
            "phase_display": engagement.get_current_phase_display(),
            "event_title": engagement.event.title if engagement.event else "",
        },
    )


def assign_team_member(portal: ClientPortal, team_member_id: str) -> PortalTeamAssignment:
    """
    Assign a team member to a portal.
    No-ops (returns existing) if the assignment already exists.
    Staff-only — caller must enforce permission before calling.
    """
    team_member = TeamMember.objects.filter(id=team_member_id).first()
    if not team_member:
        raise ValidationError("Team member not found.")

    assignment, _ = PortalTeamAssignment.objects.get_or_create(portal=portal,team_member=team_member,)
    return assignment


def seed_default_team_members(portal: ClientPortal) -> int:
    """
    Assign every TeamMember flagged is_default to a portal — the "Meet Your Team"
    contacts every portal starts with. Called on portal creation (signal).
    Idempotent: get_or_create + the (portal, team_member) unique constraint mean
    re-running never duplicates, and a member the staff later removed from this
    portal isn't re-added (seeding only fires on creation). Returns how many were
    newly assigned.
    """
    created = 0
    for member in TeamMember.objects.filter(is_default=True):
        _, was_created = PortalTeamAssignment.objects.get_or_create(portal=portal, team_member=member)
        created += int(was_created)
    return created


def remove_team_member(portal: ClientPortal, team_member_id: str) -> None:
    """
    Remove a team member assignment from a portal.
    Staff-only — caller must enforce permission before calling.
    """
    from django.core.exceptions import ValidationError as DjangoValidationError
    try:
        deleted, _ = PortalTeamAssignment.objects.filter(portal=portal,team_member_id=team_member_id,).delete()
    except DjangoValidationError:
        raise ValidationError("Invalid team member ID format.")

    if not deleted:
        raise ValidationError("This team member is not assigned to the portal.")

def get_engagement_content_summary(engagement: EventEngagement | None) -> dict:
    """
    Count what's attached to an engagement — meetings, conversations,
    reminders, documents, invoices, receipts, payment milestones. Used to
    warn staff what's about to stop being shown when switching the active
    event (see docs/FAILURE_POINTS_AUDIT.md F5): switching is non-destructive
    (nothing is deleted, it just stops being the active engagement), but
    looks identical to real data loss from the client's side without a
    warning, since meetings/conversations/etc. all read through
    active_engagement.
    """
    if engagement is None:
        return {}

    payment_schedule = getattr(engagement, "payment_schedule", None)
    return {
        "meetings": engagement.meetings.count(),
        "conversations": engagement.conversations.count(),
        "reminders": engagement.reminders.count(),
        "documents": engagement.documents.count(),
        "client_documents": engagement.client_documents.count(),
        "invoices": engagement.invoices.count(),
        "receipts": engagement.receipts.count(),
        "payment_milestones": payment_schedule.milestones.count() if payment_schedule else 0,
    }


def activate_engagement(portal: ClientPortal, event) -> EventEngagement:
    """
    Deactivate all current engagements for the portal,
    then create (or reactivate) an engagement for the new event.
    Staff-only — caller enforces permission.
    """
    with transaction.atomic():
        portal.engagements.filter(is_active=True).update(is_active=False)
        engagement, _ = EventEngagement.objects.get_or_create(portal=portal,event=event,defaults={"is_active": True},)
        if not engagement.is_active:
            engagement.is_active = True
            engagement.save(update_fields=["is_active", "updated_at"])
    return engagement