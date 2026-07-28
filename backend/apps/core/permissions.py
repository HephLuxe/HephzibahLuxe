# apps/core/permissions.py
from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied


# ── DRF class (request-level only, for decorators) ─────────────

class IsStaffOrSuperuser(BasePermission):
    """For @permission_classes decorator — auto-evaluated at request level."""
    def has_permission(self, request, view):
        return request.user and (request.user.is_staff or request.user.is_superuser)


# ── Helper functions (for object-level checks in FBV bodies) ───

def is_staff_or_superuser(user):
    """Hephzibah Luxe team — full management access."""
    return user.is_staff or user.is_superuser


def is_event_celebrant(user, event):
    """The client who owns this event."""
    return event.celebrant == user


def is_portal_owner(user, portal):
    """The client who owns this portal."""
    return portal.user == user


# ── Composite checks (common patterns used across views) ───────

def can_access_event(user, event):
    """Staff sees any event, client sees only their own."""
    return is_staff_or_superuser(user) or is_event_celebrant(user, event)


def can_access_portal(user, portal):
    """Staff sees any portal, client sees only their own."""
    return is_staff_or_superuser(user) or is_portal_owner(user, portal)


def can_access_portal_resource(user, obj):
    """For meetings, conversations, reminders — anything with obj.portal."""
    return is_staff_or_superuser(user) or is_portal_owner(user, obj.portal)


# ── Enforcer (raises instead of returning bool) ───────────────

def enforce(condition, message="Permission denied"):
    """Shorthand to raise PermissionDenied if check fails."""
    if not condition:
        raise PermissionDenied(message)