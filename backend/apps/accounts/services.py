"""
apps/accounts/services.py

Account lifecycle operations. Deactivation lives here (rather than inline in the
view or the admin action) so the API endpoint and the Django admin can't drift
apart — both call the same two functions.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from . import developers


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

    Raises PermissionDenied if aimed at a developer account — by ANYONE,
    including another developer and including that developer themselves.

    Nobody, rather than "nobody but a peer", because ``User.save()`` re-derives
    ``is_active=True`` for a developer unconditionally
    (``developers.expected_state``). A permitted call would therefore write the
    deactivation, have it corrected on the way to the database, and still return
    ``changed: True`` — a success message describing something that did not
    happen, which is the precise failure this project rejects elsewhere. Matching
    the guard to the derived state keeps the two from disagreeing, and makes
    deactivation consistent with deletion, which the ``pre_delete`` signal
    refuses for every actor too.

    Retiring a developer is therefore always the same two steps: remove the
    address from ``PLATFORM_DEVELOPER_EMAILS`` and redeploy, after which this
    function treats the account like any other.

    The guard is here, in the service, rather than in the two callers, for the
    same reason the self-deactivation check is: the API endpoint and the admin
    action both come through this function, so a check placed here cannot be
    missed by one of them.
    """
    if developers.is_developer(user):
        raise PermissionDenied(
            f"{user.email} is a protected developer account and cannot be "
            "deactivated. Remove the address from PLATFORM_DEVELOPER_EMAILS and "
            "redeploy first if this is intentional."
        )

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
