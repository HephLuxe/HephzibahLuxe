import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import PasswordResetToken, User

######################################################################## Reset Password #####################################################################

def generate_reset_code() -> str:
    """Generate a secure 6-digit code"""
    return ''.join([str(secrets.randbelow(10)) for _ in range(6)])


def create_password_reset_token(user: User, ip_address: str = '') -> PasswordResetToken:
    """
    Create a password reset token for a user.
    Invalidates any existing unused tokens for this user.
    """
    PasswordResetToken.objects.filter(
        user=user,
        is_used=False
    ).update(is_used=True, used_at=timezone.now())

    code = generate_reset_code()

    token = PasswordResetToken.objects.create(
        user=user,
        code=code,
        expires_at=timezone.now() + timedelta(minutes=15),
        ip_address=ip_address
    )

    return token


def send_password_reset_email(user: User, code: str) -> bool:
    """Send password reset code via email (queue_notification -> Celery -> Brevo)."""
    from apps.notifications.services import queue_notification

    queue_notification(
        recipient_email=user.email,
        recipient_user=user,
        template_name="password_reset",
        context={
            "user_first_name": user.first_name,
            "code": code,
            "expires_in_minutes": 15,
        },
    )
    return True


def verify_reset_code(email: str, code: str) -> tuple[bool, PasswordResetToken | str]:
    """
    Verify a password reset code.
    Returns (is_valid, token_or_error_message)
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return False, "Invalid email or code"

    try:
        token = PasswordResetToken.objects.get(
            user=user,
            code=code,
            is_used=False
        )
    except PasswordResetToken.DoesNotExist:
        return False, "Invalid or expired code"

    if token.is_expired():
        return False, "Code has expired"

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
    """Send login credentials to a newly registered user (queue_notification -> Celery -> Brevo)."""
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
