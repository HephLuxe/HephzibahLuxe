# apps/core/permissions.py
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

# ── DRF class (request-level only, for decorators) ─────────────

class IsStaffOrSuperuser(BasePermission):
    """For @permission_classes decorator — auto-evaluated at request level."""
    def has_permission(self, request, view):
        return request.user and (request.user.is_staff or request.user.is_superuser)


class IsDeveloper(BasePermission):
    """The narrowest gate on the platform: a configured developer account.

    Nothing uses it yet, and that is correct — a developer already passes every
    ``IsStaffOrSuperuser`` check by virtue of ``is_staff``/``is_superuser``, so
    no existing endpoint needs changing. This exists for the endpoints that
    should be developer-only when they arrive (a kill switch, a feature-flag
    editor, anything that reconfigures the platform rather than operates it).
    """
    def has_permission(self, request, view):
        return is_developer(request.user)


# ── Helper functions (for object-level checks in FBV bodies) ───

def is_developer(user):
    """The platform's developer — above admin, and not revocable on-platform.

    Thin re-export of ``accounts.developers.is_developer`` so that view code can
    keep importing all its permission helpers from one module. The authority
    lives there, and it reads the deployment environment rather than the `role`
    column; see apps/accounts/developers.py.
    """
    from apps.accounts.developers import is_developer as _is_developer

    return _is_developer(user)


def is_staff_or_superuser(user):
    """Hephzibah Luxe team — full management access.

    Unchanged by the developer role, and deliberately so: ``User.save()`` gives
    a developer both ``is_staff`` and ``is_superuser``, so every one of the ~40
    call sites across the apps admits them without edit. A role that needed
    fifteen apps changed to recognise it would be a role that gets missed
    somewhere.
    """
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