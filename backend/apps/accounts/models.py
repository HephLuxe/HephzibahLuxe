from django.conf import settings
from django.db import models
from django.contrib.auth import models as auth_models
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from django.db.models import Q, F


class UserRole(models.TextChoices):
    CLIENT = "client", "Client"
    STAFF = "staff", "Staff"
    ADMIN = "admin", "Admin"

class UserManager(auth_models.BaseUserManager):
    def create_user(self, first_name, last_name, email, password=None, role=UserRole.CLIENT) -> "User":
        if not email:
            raise ValueError("User must have an email")
        if not first_name:
            raise ValueError("User must have a first name")
        if not last_name:
            raise ValueError("User must have a last name")

        user = self.model(email=self.normalize_email(email))
        user.first_name = first_name
        user.last_name = last_name
        user.role = role
        user.set_password(password)
        user.is_active = True
        user.save()
        return user

    def create_superuser(self, first_name, last_name, email, password, **extra_fields) -> "User":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(
            first_name=first_name,
            last_name=last_name,
            email=email,
            password=password,
            role=UserRole.ADMIN,
        )

class User(auth_models.AbstractUser):
    first_name = models.CharField(verbose_name="First Name", max_length=255)
    last_name = models.CharField(verbose_name="Last Name", max_length=255)
    email = models.EmailField(verbose_name="Email", max_length=255, unique=True)
    password = models.CharField(max_length=255)
    username = None

    role = models.CharField(
        max_length=10,
        choices=UserRole.choices,
        default=UserRole.CLIENT,
    )

    force_password_change = models.BooleanField(default=False)
    temporary_password_created_at = models.DateTimeField(null=True, blank=True)

    # ── Offboarding ──────────────────────────────────────────────
    # Deactivation is a reversible state, not a delete: `is_active` (inherited
    # from AbstractUser) is the switch, and these three record the context so an
    # offboarding can be reviewed and undone. SimpleJWT checks is_active on every
    # authenticated request (CHECK_USER_IS_ACTIVE defaults True), so flipping it
    # locks the account out immediately — no waiting for a token to expire.
    #
    # All three are cleared on reactivation: they describe the CURRENT
    # deactivation, so a populated deactivated_at always means "off right now".
    # See accounts.services.deactivate_user / reactivate_user — the single path
    # used by both the API endpoint and the admin action.
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="deactivated_users",
    )
    deactivation_reason = models.CharField(max_length=255, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    def save(self, *args, **kwargs):
        # Keep is_staff and is_superuser in sync with role
        self.is_staff = self.role in (UserRole.STAFF, UserRole.ADMIN)
        self.is_superuser = self.role == UserRole.ADMIN
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class PasswordResetToken(models.Model):
    # Stores 6-digit verification codes for password reset.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='password_reset_tokens'
    )
    code = models.CharField(max_length=6)  # 6-digit code
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.CharField(max_length=45, blank=True)  # Track requesting IP

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_used', 'expires_at']),
            models.Index(fields=['code', 'is_used']),
        ]

    def is_expired(self):
        from django.utils import timezone
        return timezone.now() > self.expires_at

    def is_valid(self):
        return not self.is_used and not self.is_expired()

    def __str__(self):
        return f"Reset token for {self.user.email} - {self.code}"


