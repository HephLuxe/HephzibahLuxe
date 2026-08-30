"""
apps/accounts/tasks.py

Scheduled housekeeping for auth state. Both tasks run from cron group
`daily_maintenance` (apps/core/management/commands/run_scheduled.py).

Neither existed before. `flushexpiredtokens` shipped with SimpleJWT and was
never scheduled anywhere; reset tokens were marked used and then kept forever.
Both tables only ever grew, and the second one grew holding a plaintext 6-digit
code, the requesting IP, and the user it belonged to.
"""

import logging
from datetime import timedelta

from django.core.management import call_command
from django.utils import timezone

from apps.core.background import background_task

logger = logging.getLogger(__name__)

# How long an expired-or-used reset token is kept before deletion. Long enough
# to answer "did a reset happen last week, and from where?" during an incident;
# short enough that the codes and IP addresses are not an indefinite liability.
RESET_TOKEN_RETENTION_DAYS = 7


@background_task(name="accounts.flush_expired_jwt")
def flush_expired_jwt_tokens() -> None:
    """
    Delete blacklisted/outstanding JWTs whose refresh token has expired.

    `rest_framework_simplejwt.token_blacklist` is installed with
    ROTATE_REFRESH_TOKENS and BLACKLIST_AFTER_ROTATION both on, so every single
    refresh writes an OutstandingToken *and* a BlacklistedToken. With a 1-hour
    access token an active session does that ~9 times a working day, per user.
    SimpleJWT ships `flushexpiredtokens` for exactly this and it was never
    wired to anything — the tables had no delete path at all.
    """
    from apps.notifications.models import ScheduledTaskSettings

    if not ScheduledTaskSettings.is_task_enabled("accounts_flush_expired_jwt"):
        return

    call_command("flushexpiredtokens")


@background_task(name="accounts.prune_reset_tokens")
def prune_expired_reset_tokens() -> None:
    """
    Delete PasswordResetToken rows that expired more than
    RESET_TOKEN_RETENTION_DAYS ago.

    Keyed on `expires_at`, not `is_used`: a used token and an abandoned one are
    equally dead, and expiry is the one timestamp every row has. Anything still
    inside its window is left alone regardless of state.
    """
    from apps.notifications.models import ScheduledTaskSettings

    from .models import PasswordResetToken

    if not ScheduledTaskSettings.is_task_enabled("accounts_prune_reset_tokens"):
        return

    cutoff = timezone.now() - timedelta(days=RESET_TOKEN_RETENTION_DAYS)
    deleted, _ = PasswordResetToken.objects.filter(expires_at__lt=cutoff).delete()
    if deleted:
        logger.info(
            "pruned %s expired password-reset token(s)",
            deleted,
            extra={"event": "reset_tokens_pruned", "count": deleted},
        )
