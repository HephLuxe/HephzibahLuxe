from rest_framework import serializers

from apps.core.serializers import AttributionSerializerMixin
from apps.core.uploads import validate_photo
from apps.core.utils import user_display_name

from .models import (
    ClientPortal,
    EventEngagement,
    PlanningPhase,
    PortalSettings,
    PortalTeamAssignment,
    TeamMember,
)
from .services import PHASE_ORDER

# ── TeamMember serializers ──────────────────────────────────────

class TeamMemberSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    """Full detail — used when creating/reading team member profiles.

    `is_default` flags a member as auto-assigned to every new portal — staff set
    it here (this is the endpoint for adjusting the "Meet Your Team" defaults).
    """
    class Meta:
        model = TeamMember
        fields = [
            "id", "name", "role", "bio", "photo",
            "email", "phone", "is_default", "created_by_display", "last_updated_by_display",
        ]

    def validate_photo(self, value):
        return validate_photo(value)


class TeamMemberListSerializer(serializers.ModelSerializer):
    """Minimal — used when listing team members assigned to a portal."""
    class Meta:
        model = TeamMember
        fields = ["id", "name", "role", "bio", "photo"]


# ── PortalTeamAssignment serializers ───────────────────────────

class PortalTeamAssignmentSerializer(serializers.ModelSerializer):
    """Used when assigning a team member to a portal (staff action)."""
    class Meta:
        model = PortalTeamAssignment
        fields = ["id", "portal", "team_member"]


class AssignTeamMemberSerializer(serializers.Serializer):
    """Input-only: staff provides team_member_id to assign."""
    team_member_id = serializers.UUIDField()

    def validate_team_member_id(self, value: str) -> str:
        if not TeamMember.objects.filter(id=value).exists():
            raise serializers.ValidationError("Team member not found.")
        return value


# ── ClientPortal serializers ────────────────────────────────────

class PortalOverviewSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    title = serializers.SerializerMethodField()
    team = serializers.SerializerMethodField()
    contact = serializers.SerializerMethodField()
    # Engagement-level fields — sourced from the active engagement
    active_event = serializers.SerializerMethodField()
    current_phase = serializers.SerializerMethodField()
    current_phase_display = serializers.SerializerMethodField()
    phase_index = serializers.SerializerMethodField()
    total_phases = serializers.SerializerMethodField()
    phase_details = serializers.SerializerMethodField()
    phase_updated_by_display = serializers.SerializerMethodField()
    phase_updated_at = serializers.SerializerMethodField()
    contacts_locked = serializers.SerializerMethodField()
    event_details_locked = serializers.SerializerMethodField()
    engagement_id = serializers.SerializerMethodField()

    class Meta:
        model = ClientPortal
        fields = [
            "id", "title", "welcome_message",
            "engagement_id", "active_event",
            "current_phase", "current_phase_display",
            "phase_index", "total_phases",
            "phase_details", "phase_updated_by_display", "phase_updated_at",
            "contacts_locked", "event_details_locked",
            "team", "contact",
            "created_at", "updated_at",
            "created_by_display", "last_updated_by_display",
        ]

    def _get_engagement(self, obj: ClientPortal) -> EventEngagement | None:
        return obj.active_engagement

    def get_title(self, obj: ClientPortal) -> str | None:
        eng = self._get_engagement(obj)
        return eng.event.title if eng and eng.event else None

    def get_active_event(self, obj: ClientPortal) -> str | None:
        eng = self._get_engagement(obj)
        return str(eng.event.slug) if eng and eng.event else None

    def get_engagement_id(self, obj: ClientPortal) -> str | None:
        eng = self._get_engagement(obj)
        return str(eng.id) if eng else None

    def get_current_phase(self, obj: ClientPortal) -> str | None:
        eng = self._get_engagement(obj)
        return eng.current_phase if eng else None

    def get_current_phase_display(self, obj: ClientPortal) -> str | None:
        eng = self._get_engagement(obj)
        return eng.get_current_phase_display() if eng else None

    def get_phase_index(self, obj: ClientPortal) -> int | None:
        """1-based position in PHASE_ORDER — drives the "3 OF 6 PHASES" gauge."""
        eng = self._get_engagement(obj)
        if not eng:
            return None
        return PHASE_ORDER.index(eng.current_phase) + 1

    def get_total_phases(self, obj: ClientPortal) -> int:
        return len(PHASE_ORDER)

    def get_phase_details(self, obj: ClientPortal) -> dict:
        eng = self._get_engagement(obj)
        return eng.phase_details if eng else {}

    def get_phase_updated_by_display(self, obj: ClientPortal) -> str:
        eng = self._get_engagement(obj)
        return user_display_name(eng.phase_updated_by) if eng else ""

    def get_phase_updated_at(self, obj: ClientPortal):
        eng = self._get_engagement(obj)
        return eng.phase_updated_at if eng else None

    def get_contacts_locked(self, obj: ClientPortal) -> bool:
        eng = self._get_engagement(obj)
        return eng.contacts_locked if eng else False

    def get_event_details_locked(self, obj: ClientPortal) -> bool:
        eng = self._get_engagement(obj)
        return eng.event_details_locked if eng else False

    def get_team(self, obj: ClientPortal) -> list:
        assignments = obj.team_assignments.select_related("team_member").all()
        members = [a.team_member for a in assignments]
        return TeamMemberListSerializer(members, many=True).data

    def get_contact(self, obj: ClientPortal) -> dict:
        config = PortalSettings.load()
        return {"email": config.contact_email, "whatsapp": config.contact_whatsapp}


class PortalUpdateSerializer(serializers.ModelSerializer):
    """Staff-only: update welcome message only. Phase and event switching have dedicated endpoints."""
    class Meta:
        model = ClientPortal
        fields = ["welcome_message"]


class PhaseUpdateSerializer(serializers.Serializer):
    """Staff-only: advance or set the planning phase."""
    phase = serializers.ChoiceField(choices=PlanningPhase.choices)


class ActivateEventSerializer(serializers.Serializer):
    """Staff-only: identify which event should become the portal's active engagement."""
    event_slug = serializers.SlugField()

    def validate_event_slug(self, value: str) -> str:
        from apps.events.models import Event
        if not Event.objects.filter(slug=value).exists():
            raise serializers.ValidationError("Event not found.")
        return value