"""
apps/accounts/signals.py

The delete half of the developer protection (apps/accounts/developers.py).

``User.save()`` covers every *write* to a developer row — it re-derives role,
is_active, is_staff and is_superuser from the environment on the way past, so no
save can demote or deactivate one. Deletes do not go through ``save()``, and
``Model.delete()`` does not go through the admin's ``has_delete_permission``
either. A single line in a shell, a management command, or a cascade from some
future related model would take the account away with nothing to stop it.

``pre_delete`` is the one hook every one of those paths passes through:
``QuerySet.delete()``, ``Model.delete()``, and cascades all fire it. Raising
here aborts the whole transaction, which is the behaviour wanted — a bulk delete
that happens to include a developer should fail loudly and change nothing,
rather than delete the other rows and skip this one.

Retiring a developer account is done by removing the address from
``PLATFORM_DEVELOPER_EMAILS`` and redeploying. It is then an ordinary admin
account and deletes normally. That extra step is the whole point: it takes
access to the deployment config, which is a different credential from a platform
login.
"""

import logging

from django.conf import settings
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from . import developers

logger = logging.getLogger(__name__)


class ProtectedAccountError(Exception):
    """Raised instead of deleting a developer account.

    A plain ``Exception``, not ``PermissionDenied``: DRF maps that to a 403 and
    the Django admin catches it, and either would let a caller treat this as a
    routine "not allowed" and move on. This is a last-resort integrity guard on
    a path that should never have been reached — the admin, the API and the
    services layer all refuse long before here — so it is meant to surface as a
    500 and an alert, not as a tidy denial.
    """


@receiver(pre_delete, sender=settings.AUTH_USER_MODEL, dispatch_uid="protect_developer_accounts")
def protect_developer_accounts(sender, instance, **kwargs):
    if not developers.is_developer(instance):
        return

    logger.error(
        "Blocked an attempt to delete a protected developer account.",
        extra={
            "event": "developer_account_delete_blocked",
            "user_id": str(instance.pk),
        },
    )
    raise ProtectedAccountError(
        f"{instance.email} is a protected developer account and cannot be "
        "deleted. Remove the address from PLATFORM_DEVELOPER_EMAILS and "
        "redeploy first if this is intentional."
    )
