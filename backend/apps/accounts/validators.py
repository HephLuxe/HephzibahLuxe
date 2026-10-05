"""
apps/accounts/validators.py

The platform password policy, as a Django password validator so every path that
calls ``django.contrib.auth.password_validation.validate_password`` — the API
endpoints, the admin's add-user and change-password forms, an interactive
``createsuperuser`` — enforces the same rules from one place
(settings.AUTH_PASSWORD_VALIDATORS).

Rules: at least ``min_length`` characters, not entirely numeric, at least one
uppercase letter, at least one special (non-alphanumeric) character.

All failures are reported together rather than one at a time, so a user fixing
a rejected password sees every rule they missed in a single response.

Existing stored passwords are unaffected: validators only run when a password is
being set.
"""

from django.core.exceptions import ValidationError


class PasswordPolicyValidator:
    def __init__(self, min_length: int = 8):
        self.min_length = min_length

    def validate(self, password: str, user=None) -> None:
        errors = []
        if len(password) < self.min_length:
            errors.append(ValidationError(
                "Password must be at least %(min_length)d characters long.",
                code="password_too_short",
                params={"min_length": self.min_length},
            ))
        if password.isdigit():
            errors.append(ValidationError(
                "Password cannot be entirely numeric.",
                code="password_entirely_numeric",
            ))
        if not any(c.isupper() for c in password):
            errors.append(ValidationError(
                "Password must contain at least one uppercase letter.",
                code="password_no_uppercase",
            ))
        if not any(not c.isalnum() for c in password):
            errors.append(ValidationError(
                "Password must contain at least one special character (e.g. ! @ # $ % * - _).",
                code="password_no_special",
            ))
        if errors:
            raise ValidationError(errors)

    def get_help_text(self) -> str:
        return (
            f"Your password must be at least {self.min_length} characters long, cannot be "
            "entirely numeric, and must contain at least one uppercase letter and one "
            "special character."
        )
