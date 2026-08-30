import logging
import secrets
import string
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from .models import PasswordResetToken, User

logger = logging.getLogger(__name__)

######################################################################## Reset Password #####################################################################

# How long a reset code stays valid. Raised from 15 minutes when in-task retry
# was removed with Celery: a failed send is now re-driven only by the
# `notification_retry` cron sweep, so the worst-case delay before the second
# attempt is the sweep's cadence (*/10) rather than the old 5-minute
# `self.retry`. At 15 minutes a single transient Brevo blip could land the email
# after the code it contains was already dead — the user gets a code that cannot
# work and no explanation. Thirty minutes keeps a comfortable margin.
#
# Safe at this length: a 6-digit code is 10^6 possibilities against the existing
# 10/min + 50/day verify limits (apps/core/ratelimit.py), so a 30-minute window
# buys an attacker on the order of 300 guesses.
RESET_CODE_TTL_MINUTES = 30

# Returned once a token has spent MAX_VERIFY_ATTEMPTS. Deliberately distinct from
# the generic "invalid" string: to see it at all you must already be submitting
# codes for a known address, so it reveals nothing the request endpoint doesn't
# already refuse to reveal (that one always answers 200) — and without it a real
# user who mistyped five times would keep guessing against a dead token with no
# idea why nothing works.
TOO_MANY_ATTEMPTS_MESSAGE = "Too many incorrect attempts. Please request a new code."


def generate_reset_code() -> str:
    """Generate a secure 6-digit code"""
    return ''.join([str(secrets.randbelow(10)) for _ in range(6)])


def create_password_reset_token(user: User, ip_address: str = '') -> tuple[PasswordResetToken, str]:
    """
    Create a password reset token for a user.
    Invalidates any existing unused tokens for this user.

    Returns ``(token, code)``. The plaintext code is returned rather than stored:
    only its hash goes to the database (see PasswordResetToken), so this call is
    the *only* moment it exists, and the caller must mail it immediately or lose
    it. That is the intent — there is no way to recover a code afterwards, for us
    or for anyone reading the table.
    """
    PasswordResetToken.objects.filter(
        user=user,
        is_used=False
    ).update(is_used=True, used_at=timezone.now())

    code = generate_reset_code()

    token = PasswordResetToken.objects.create(
        user=user,
        code_hash=make_password(code),
        expires_at=timezone.now() + timedelta(minutes=RESET_CODE_TTL_MINUTES),
        ip_address=ip_address
    )

    return token, code


def send_password_reset_email(user: User, code: str) -> bool:
    """Send password reset code via email (queue_notification -> Brevo).

    `expires_in_minutes` is read from RESET_CODE_TTL_MINUTES rather than
    hardcoded, so the number in the email body can never drift from the number in
    `expires_at`.

    Note the code lands in `Notification.context`, which is why send_now redacts
    that field for this template once the row reaches a terminal state.
    """
    from apps.notifications.services import queue_notification

    queue_notification(
        recipient_email=user.email,
        recipient_user=user,
        template_name="password_reset",
        context={
            "user_first_name": user.first_name,
            "code": code,
            "expires_in_minutes": RESET_CODE_TTL_MINUTES,
        },
    )
    return True


def verify_reset_code(email: str, code: str) -> tuple[bool, PasswordResetToken | str]:
    """
    Verify a password reset code.
    Returns (is_valid, token_or_error_message)

    The code is stored hashed with a per-row salt, so this can no longer look a
    token up *by* the code. It fetches the user's outstanding token instead and
    checks the code against it — which works because
    ``create_password_reset_token`` invalidates prior unused tokens, leaving at
    most one.

    Every wrong guess spends one of ``MAX_VERIFY_ATTEMPTS``, and the fifth burns
    the token outright. Without that, a code was guessable for its entire
    lifetime and the only ceiling was the per-IP verify limits — which is exactly
    the exposure that lengthening the TTL to 30 minutes would otherwise have
    widened.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return False, "Invalid email or code"

    token = (
        PasswordResetToken.objects
        .filter(user=user, is_used=False)
        .order_by("-created_at")
        .first()
    )
    if token is None:
        return False, "Invalid or expired code"

    if token.is_expired():
        return False, "Code has expired"

    # Checked before the hash comparison so a burned token costs no KDF work.
    if token.attempts_exhausted():
        return False, TOO_MANY_ATTEMPTS_MESSAGE

    if not check_password(code, token.code_hash):
        token.attempt_count += 1
        token.save(update_fields=["attempt_count"])
        exhausted = token.attempts_exhausted()

        logger.warning(
            "password reset code rejected",
            extra={
                "event": "reset_code_rejected",
                "user_id": str(user.id),
                "attempt_count": token.attempt_count,
                "token_burned": exhausted,
            },
        )
        return False, TOO_MANY_ATTEMPTS_MESSAGE if exhausted else "Invalid or expired code"

    return True, token


######################################################################## Temp Login code #####################################################################

def generate_temporary_password(length: int | None = None) -> str:
    """
    Generate a random alphanumeric temporary password.
    """
    if length is None:
        length = secrets.choice(range(12, 17))

    alphabet = string.ascii_letters + string.digits
    password = ''.join(secrets.choice(alphabet) for _ in range(length))

    return password


def send_user_credentials_email(user: User, temporary_password: str) -> bool:
    """Send login credentials to a newly registered user (queue_notification -> Brevo).

    The generated password lands in `Notification.context`; send_now redacts it
    once the row is SENT or ABANDONED (see notifications.services
    .AUTH_SECRET_TEMPLATES).
    """
    from apps.core import deeplinks
    from apps.notifications.services import queue_notification

    display_name = f"{user.first_name} {user.last_name}".strip() or user.email.split("@")[0]
    queue_notification(
        recipient_email=user.email,
        recipient_user=user,
        template_name="user_credentials",
        context={
            "display_name": display_name,
            "user_email": user.email,
            "temporary_password": temporary_password,
            "login_url": deeplinks.login_url(),
        },
    )
    return True
