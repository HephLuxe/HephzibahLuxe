"""
apps/accounts/developers.py

The `developer` role — the one role above admin, and the only one an admin
cannot revoke.

Why this exists
---------------
Every other role on this platform is administered by `role=admin` accounts.
`User.save()` gives those accounts `is_superuser=True`, which in Django means
the admin site grants them everything unconditionally, so an admin can currently
do all of the following to any account, including the one that built the
platform:

  * change its `role` (demote it to `client`),
  * change its `email` — which is USERNAME_FIELD, so this hands the account's
    login identity and every future password-reset code to a new inbox,
  * set its password outright through the admin's change-password form,
  * untick `is_active`, or run the "Deactivate (offboard)" action,
  * `DELETE` the row.

That is the correct amount of power for an admin over a client or a staff
member. It is the wrong amount of power to hand to a contractor's test account,
which is exactly what an admin login given to a frontend developer is. One
misclick or one bad actor and the platform's owner is locked out of their own
system with no way back in that does not involve shell access to the server.

The two halves of the answer
----------------------------
**1. The environment is the authority, not the database.**
``is_developer_email`` reads ``settings.PLATFORM_DEVELOPER_EMAILS`` and nothing
else. No permission decision anywhere consults ``User.role`` to establish
developer-ness. The consequence is the property that matters: an admin who
somehow rewrites the `role` column — through a surface we missed, a raw SQL
session, a restored backup — has changed a label and nothing more. The column is
a mirror maintained for display and for ``?role=`` filtering; see
``config/settings.py`` where the setting is declared.

Removing a developer therefore requires the deployment config (the Render
dashboard, the server's `.env`), which is a different credential from a platform
login and is not something a contractor's admin account can reach.

**2. Every mutation surface refuses.** ``enforce_can_manage`` is called from
``accounts.services``, ``accounts.serializers``, ``accounts.admin`` and the
``pre_delete`` signal. A developer account is *visible* to admins — it appears
in the Django admin changelist and in ``GET /users/`` — but read-only: every
edit, action, password change and delete against it is refused with a clear
message rather than silently ignored. Visible-and-locked beats hidden, because
an admin who cannot see why something is failing files a bug or works around it.

Layer 1 is what makes layer 2 safe to get wrong. If a new admin action ships
next year without a guard, the worst it achieves is corrupting a mirror column
that ``repair`` puts back at the next sign-in.

Self-repair
-----------
``repair`` re-derives the four fields that follow from developer-ness (`role`,
`is_active`, `is_staff`, `is_superuser`) and writes them back if they have
drifted. It runs from ``User.save()`` — so no ORM save can leave a developer
demoted — and, critically, from both login paths *before* authentication.

The pre-authentication call is not decoration. Django's ``ModelBackend`` and
SimpleJWT both read ``is_active`` off the row and refuse an inactive user before
any of this module's code would otherwise run, so a developer deactivated by a
``queryset.update()`` (which bypasses ``save()``) would be locked out despite
being in the env list. Repairing on the way in closes that: the row is corrected,
then authentication proceeds against a correct row.

What this does NOT defend against
---------------------------------
Anyone with the deployment environment, or a shell on the server, or write
access to the database *and* the ability to edit env vars. That is deliberate —
those are the credentials that define who runs the platform, and no in-app
control can be stronger than them. The threat modelled here is a hostile or
careless **platform admin**, which is the account type actually being handed
out.
"""

from __future__ import annotations

from django.conf import settings

# ── The authority ────────────────────────────────────────────────────────────

def developer_emails() -> frozenset[str]:
    """The configured developer addresses, lowercased.

    Read through ``getattr`` rather than a direct attribute access so that a
    settings module without the key (an old deploy, a test using
    ``@override_settings`` to remove it) degrades to "no developers" instead of
    raising ``AttributeError`` from inside ``User.save()`` — which would take
    down every write on the platform to protect one account.

    Not cached: ``override_settings`` in tests must be able to change it, and
    the list is a handful of strings compared against a set. The cost is
    nothing next to the query the caller is already making.
    """
    return frozenset(getattr(settings, "PLATFORM_DEVELOPER_EMAILS", ()) or ())


def is_developer_email(email: str | None) -> bool:
    """Is this address a developer? The one function that decides.

    Case-insensitive, because ``User.email`` stores what was typed while the env
    var is normalised at boot: matching raw would let ``Ade@Example.com`` in the
    database silently fail against ``ade@example.com`` in the config, and the
    symptom of that failure is the developer quietly losing their protection.
    """
    if not email:
        return False
    return email.strip().lower() in developer_emails()


def is_developer(user) -> bool:
    """Is this user a developer? Safe on ``AnonymousUser`` and on ``None``.

    Deliberately does not consult ``user.role``. See the module docstring: the
    column is a mirror, and a mirror that disagrees must lose.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return is_developer_email(getattr(user, "email", None))


# ── The guard ────────────────────────────────────────────────────────────────

PROTECTED_MESSAGE = (
    "This is a protected developer account. It cannot be edited, deactivated, "
    "renamed, have its password changed, or be deleted by anyone else — "
    "including administrators. Developer accounts are configured in the "
    "deployment environment (PLATFORM_DEVELOPER_EMAILS), not on the platform."
)

GRANT_MESSAGE = (
    "The developer role cannot be granted from here. It is held by the "
    "addresses listed in the PLATFORM_DEVELOPER_EMAILS deployment setting, and "
    "assigning the role to an account not in that list would create a row "
    "claiming a privilege it does not have."
)


def can_manage(actor, target) -> bool:
    """May ``actor`` modify ``target``?

    True for everything except the one case this module exists for: a
    non-developer acting on a developer. Note that a developer acting on
    *themselves* is allowed — the protection is against other people, not
    against you changing your own name or password.

    ``actor is None`` means an unattributed caller (a management command, a
    migration, a shell). Those are refused too. Anything running with no actor
    is either a system path that has no business editing a developer, or a
    person on the server — and a person on the server has the environment,
    which is the supported way to do this.
    """
    if not is_developer(target):
        return True
    return is_developer(actor)


def enforce_can_manage(actor, target, message: str = PROTECTED_MESSAGE) -> None:
    """``can_manage`` as a refusal. Raises DRF's ``PermissionDenied`` (403).

    DRF's exception rather than Django's because every caller is either a DRF
    view or the Django admin, and DRF's is the one this project's
    ``custom_exception_handler`` renders into the standard error envelope. The
    admin catches it explicitly where it needs to show a message instead.
    """
    from rest_framework.exceptions import PermissionDenied

    if not can_manage(actor, target):
        raise PermissionDenied(message)


def protected_queryset(queryset, actor):
    """The rows in ``queryset`` that ``actor`` may not touch.

    For bulk paths — an admin action runs against a queryset, not a loop of
    permission checks, so it has to know what to skip before it starts.

    Built from OR'd ``iexact`` terms rather than ``email__in``: the env list is
    lowercased at boot while ``User.email`` stores what was typed, so a
    case-sensitive ``IN`` would quietly fail to match ``Dev@Example.com`` and
    hand the bulk action the very row it was meant to exclude. The list is a
    handful of addresses, so the OR chain costs nothing.
    """
    if is_developer(actor):
        return queryset.none()

    emails = developer_emails()
    if not emails:
        return queryset.none()

    from django.db.models import Q

    match = Q()
    for email in emails:
        match |= Q(email__iexact=email)
    return queryset.filter(match)


# ── Self-repair ──────────────────────────────────────────────────────────────

def expected_state() -> dict:
    """The four fields that follow from being a developer.

    Takes no argument, because none of it depends on which developer: the state
    is a constant consequence of appearing in the env list. Split out from
    ``repair`` so ``User.save()`` can apply it in-memory without a second write,
    and so a test can assert on the intent rather than on a side effect.

    ``is_active=True`` is in here on purpose and is the sharpest edge of the
    whole design: it means a developer account **cannot be deactivated**, by
    anybody, including by itself. That is the requirement — an offboarding
    switch that the person being protected can trip is not protection. Retiring
    a developer account is done by removing it from the env list first, at which
    point it is an ordinary admin account and every normal control applies again.
    """
    from .models import UserRole

    return {
        "role": UserRole.DEVELOPER,
        "is_active": True,
        "is_staff": True,
        "is_superuser": True,
    }


def apply_state(user) -> list[str]:
    """Set the developer fields on ``user`` in memory. Returns what changed.

    No save — the caller decides. ``User.save()`` calls this before writing (so
    the correction rides along on a write that was happening anyway) and
    ``repair`` calls it and then persists only if something moved.
    """
    changed = []
    for field, value in expected_state().items():
        if getattr(user, field) != value:
            setattr(user, field, value)
            changed.append(field)
    return changed


def repair(user) -> list[str]:
    """Put a drifted developer row back. Returns the fields corrected.

    A no-op — and, importantly, no database write — when nothing has drifted,
    which is every sign-in but the one after an attempted demotion. This runs on
    the login path, so it must not add a write to the normal case.

    Non-developers are left completely alone: this function must never be able
    to *grant* anything.
    """
    if not is_developer(user):
        return []
    changed = apply_state(user)
    if changed:
        import logging

        logging.getLogger(__name__).warning(
            "Repaired a developer account whose row had drifted.",
            extra={
                "event": "developer_account_repaired",
                "user_id": str(user.pk),
                "fields": changed,
            },
        )
        # update_fields, not a full save: a repair on the login path must not
        # clobber a concurrent write to an unrelated column.
        user.save(update_fields=changed)
    return changed


def repair_by_email(email: str | None) -> list[str]:
    """``repair`` for the login views, which have an address before they have a
    user.

    Called *before* authentication on both login paths. One indexed lookup on an
    address already in hand, and only when that address is in the env list — so
    an ordinary sign-in, and every sign-in attempt by an attacker, costs a set
    membership test and no query at all.
    """
    if not is_developer_email(email):
        return []

    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(email__iexact=(email or "").strip()).first()
    if user is None:
        return []
    return repair(user)
