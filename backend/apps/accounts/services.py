"""
apps/accounts/services.py

Account lifecycle operations. Deactivation lives here (rather than inline in the
view or the admin action) so the API endpoint and the Django admin can't drift
apart — both call the same two functions.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError


def _revoke_refresh_tokens(user) -> int:
    """
    Blacklist every outstanding refresh token for a user.

    is_active alone already blocks API access (SimpleJWT rejects an inactive
    user on every authenticated request), but a refresh token can still be
    exchanged for a new access token without loading the user. Blacklisting
    closes that door so a deactivated session can't keep minting credentials.

    Returns how many tokens were newly blacklisted.
    """
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    revoked = 0
    for token in OutstandingToken.objects.filter(user=user):
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        revoked += int(created)
    return revoked


@transaction.atomic
def deactivate_user(user, *, by=None, reason: str = "") -> dict:
    """
    Offboard a user — reversibly.

    Deliberately not a delete: the user is the FK target of their portal, their
    events, and every created_by/last_updated_by stamp across the project.
    Deactivating preserves all of that and can be undone with reactivate_user();
    deleting would blank the attribution (SET_NULL) and cascade the portal away.

    Idempotent: deactivating an already-inactive user is a no-op that reports
    `changed: False` rather than overwriting who originally did it.

    Raises ValidationError if a user tries to deactivate themselves — an easy
    misclick that's painful to undo (you'd need another admin or a shell).
    """
    if by is not None and by.pk == user.pk:
        raise ValidationError("You cannot deactivate your own account.")

    if not user.is_active:
        return {"changed": False, "revoked_tokens": 0, "user": user}

    user.is_active = False
    user.deactivated_at = timezone.now()
    user.deactivated_by = by
    user.deactivation_reason = (reason or "").strip()[:255]
    user.save(update_fields=[
        "is_active", "deactivated_at", "deactivated_by", "deactivation_reason",
    ])

    return {"changed": True, "revoked_tokens": _revoke_refresh_tokens(user), "user": user}


@transaction.atomic
def reactivate_user(user, *, by=None) -> dict:
    """
    Restore access, clearing the deactivation record so `deactivated_at` always
    answers "is this account off right now?".

    Previously blacklisted refresh tokens stay revoked — the user simply logs in
    again for a fresh pair. Nothing else needs restoring: their portal, events,
    documents and attribution were never touched.

    Idempotent, like deactivate_user.
    """
    if user.is_active and user.deactivated_at is None:
        return {"changed": False, "user": user}

    user.is_active = True
    user.deactivated_at = None
    user.deactivated_by = None
    user.deactivation_reason = ""
    user.save(update_fields=[
        "is_active", "deactivated_at", "deactivated_by", "deactivation_reason",
    ])
    return {"changed": True, "user": user}
