from datetime import timedelta

from django.conf import settings
from django.contrib.auth import models as auth_models
from django.db import models
from django.utils import timezone

from apps.core.models import AttributedModel


class UserRole(models.TextChoices):
    CLIENT = "client", "Client"
    STAFF = "staff", "Staff"
    ADMIN = "admin", "Admin"

class UserManager(auth_models.BaseUserManager):
    def create_user(
        self, first_name, last_name, email, password=None,
        role=UserRole.CLIENT, created_by=None,
    ) -> "User":
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
        # Set BEFORE the save, not after: User's post_save signal creates the
        # ClientPortal and copies this across (apps/portal/signals.py). A staff
        # account registering a client is the only record of who onboarded them,
        # and stamping it afterwards would leave the portal's created_by NULL.
        user.created_by = created_by
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

class User(AttributedModel, auth_models.AbstractUser):
    # AttributedModel gives created_by / last_updated_by, self-referential here:
    # the staff account that registered this user. Nothing recorded that before,
    # so "who onboarded this client" was unanswerable — and the ClientPortal the
    # post_save signal creates had no actor to inherit either.
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

    # ── Failed-login tracking (ADR-0002) ─────────────────────────
    # Counted on the ACCOUNT, not on an IP, because that is the axis an IP-keyed
    # limit cannot see: a distributed guessing run sends one attempt per address,
    # so every per-IP tier stays empty while one account is ground down. Exactly
    # the shape PasswordResetToken.attempt_count already solves for reset codes,
    # and deliberately modelled on it.
    #
    # Only FAILED authentications land here (apps/accounts/login_guard.py), and
    # any success clears it — see record_failed_login / reset_failed_logins. That
    # pairing is what stops this from being a lockout weapon.
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    failed_login_at = models.DateTimeField(null=True, blank=True)

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

    # ── Notifications ─────────────────────────────────────────────────────────
    # Opt-in, not opt-out: default=False means nobody receives leads until a
    # human ticks this box. Recipients are resolved from staff accounts only.
    receives_inquiry_alerts = models.BooleanField(
        default=False, help_text="Receive an email for every inquiry submitted through the public form.",
    )

    # ── Locale ────────────────────────────────────────────────────────────────
    # The account's calendar timezone, as an IANA name ("Africa/Lagos",
    # "America/New_York"). Blank inherits settings.PLATFORM_DEFAULT_TIMEZONE.
    #
    # Not for rendering — every timestamp the API returns is UTC ISO-8601 and the
    # frontend localises it. This exists because some of this platform's fields
    # are calendar DAYS rather than instants: a PaymentMilestone.due_date and a
    # Meeting.date are naive DateFields meaning "this day, where the client
    # lives". The daily digests compare against them, and comparing a client's
    # due date to UTC's today is off by one for anyone far enough from UTC. See
    # apps/core/timezones.py.
    #
    # Deliberately a plain CharField, not choices=: the IANA database changes
    # (zones added, renamed, merged) and pinning ~600 names into a migration
    # would make every tzdata update a schema change. Validated on the way in
    # instead — by the serializer for API writes, by full_clean() in the admin.
    timezone = models.CharField(
        max_length=64, blank=True,
        help_text=(
            "IANA timezone name, e.g. Africa/Lagos. Blank uses the platform "
            "default. Sets which calendar day this account's payment-due and "
            "meeting-prep digests are computed against."
        ),
    )

    # Consecutive failed logins before the account must be recovered through a
    # password reset. Five, matching PasswordResetToken.MAX_VERIFY_ATTEMPTS —
    # same problem, same answer, and a human who has mistyped five times has
    # usually forgotten the password rather than fumbled it, so "go and reset"
    # is the thing they needed anyway.
    #
    # It must stay BELOW auth_login_account (10/h) and auth_login_account_daily
    # (50/d). Those tiers refuse with a 429 *before* this view can look at the
    # account at all, so if the ceiling were the higher of the two a locked
    # account would report a rate limit instead of the reset instruction, and the
    # recovery path would be invisible. LoginAccountDailyBackstopTests pins the
    # ordering.
    MAX_FAILED_LOGINS = 5

    # Failures older than this age out, so a one-off bad afternoon does not
    # strand an account for ever and no scheduled sweep is needed to clear it —
    # the check is lazy, on the next attempt. (RUNBOOK: no periodic job should
    # exist purely to tidy a counter; it would wake Neon for nothing.)
    FAILED_LOGIN_WINDOW = timedelta(hours=24)

    objects = UserManager()

    # Declared explicitly because AttributedModel comes first in the MRO, and
    # its (empty, abstract) Meta would otherwise shadow AbstractUser's —
    # silently dropping verbose_name/verbose_name_plural. Inheriting
    # AbstractUser.Meta keeps them; Django resets `abstract` to False for us.
    class Meta(auth_models.AbstractUser.Meta):
        pass

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    # ── Failed-login helpers ─────────────────────────────────────

    def _failures_are_stale(self) -> bool:
        """True when the last failure is old enough that the run has expired."""
        if self.failed_login_at is None:
            return True
        return timezone.now() - self.failed_login_at > self.FAILED_LOGIN_WINDOW

    def login_locked(self) -> bool:
        """Out of attempts — the account must be recovered via password reset.

        Deliberately NOT implemented with `is_active`. Offboarding uses that flag
        and carries a reason, an actor and a timestamp for review; a run of
        mistyped passwords is not an offboarding decision and must not look like
        one in the admin. It also must not require staff to intervene: the point
        is that the account holder can clear it themselves.
        """
        if self._failures_are_stale():
            return False
        return self.failed_login_count >= self.MAX_FAILED_LOGINS

    def record_failed_login(self) -> None:
        """Count one failed authentication, restarting an expired run."""
        base = 0 if self._failures_are_stale() else self.failed_login_count
        self.failed_login_count = base + 1
        self.failed_login_at = timezone.now()
        self.save(update_fields=["failed_login_count", "failed_login_at"])

    def reset_failed_logins(self) -> None:
        """Clear the run. Called on any successful authentication and on a
        completed password reset.

        Reset-on-success is what keeps ordinary use away from the ceiling: a
        person who mistypes twice and then gets in is back to zero, so only a
        genuinely unbroken run of failures ever escalates.

        It is NOT, on its own, an answer to the denial-of-service question, and
        the ADR originally overstated that. Once the ceiling is reached the
        account is refused *before* the password is checked — it has to be, or
        the ceiling would bound nothing — so a correct password cannot rescue it
        at that point. **The recovery path is the password reset**, which
        delivers a code to an inbox the attacker cannot read. The residual
        exposure is therefore "an attacker can force the account holder to
        complete a reset", not "an attacker can lock them out", and that is the
        standard trade for having any per-account bound at all.

        A no-op write is skipped so an ordinary login does not touch the row.
        """
        if self.failed_login_count == 0 and self.failed_login_at is None:
            return
        self.failed_login_count = 0
        self.failed_login_at = None
        self.save(update_fields=["failed_login_count", "failed_login_at"])

    def clean(self):
        """Reject an unknown timezone name.

        Runs from ModelForm validation, so the Django admin refuses a typo
        instead of storing it. `timezones.resolve_timezone_name` deliberately
        degrades to UTC at *read* time rather than raising — one bad row must not
        take out a whole digest run — which is precisely why the value has to be
        caught on the way in, or the fallback hides the mistake for ever.
        """
        super().clean()
        if self.timezone:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            from django.core.exceptions import ValidationError
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValidationError(
                    {"timezone": f"{self.timezone!r} is not a known IANA timezone name."}
                ) from None

    def save(self, *args, **kwargs):
        # Keep is_staff and is_superuser in sync with role
        self.is_staff = self.role in (UserRole.STAFF, UserRole.ADMIN)
        self.is_superuser = self.role == UserRole.ADMIN
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class PasswordResetToken(models.Model):
    """
    A password-reset verification code, stored **hashed**.

    The code itself is six digits — a 10^6 search space, which is small enough
    that the storage decision matters more than it would for a password:

    * **Hashed, not plaintext.** These rows used to hold the live code in the
      clear alongside the user it belonged to and the IP that asked for it, and
      were never deleted. Anyone with database access or a backup could read a
      reset code and use it inside its window.
    * **Hashed with the password hasher, not a bare digest.** SHA-256 of a
      six-digit code is not a meaningful defence: an attacker with the table
      enumerates all 10^6 digests in under a second. `make_password` uses
      Django's configured PBKDF2 (hundreds of thousands of iterations, per-row
      salt), which turns that into a per-row cost. `check_password` costs the
      same on the verify path — acceptable at the `10/m` limit on that endpoint,
      and the point.

    A per-row salt means the hash cannot be looked up by value, so
    `utils.verify_reset_code` fetches the user's outstanding token and checks the
    code against it. That is not a compromise: `create_password_reset_token`
    invalidates prior unused tokens, so there is at most one to check.

    `attempt_count` is what makes the 30-minute TTL
    (`utils.RESET_CODE_TTL_MINUTES`) safe. Without it a code stayed guessable for
    its whole window, bounded only by the per-IP verify limits — fine for a
    15-minute window, thinner for 30 minutes against a distributed source.
    """
    # Verify failures allowed against one token before it is burned. Five is
    # generous for a human mistyping six digits and leaves an attacker 5 guesses
    # per issued code instead of a whole window's worth.
    MAX_VERIFY_ATTEMPTS = 5

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='password_reset_tokens'
    )
    # PBKDF2 output from django.contrib.auth.hashers.make_password. 128 leaves
    # room for a longer hasher than the current default without a migration.
    code_hash = models.CharField(max_length=128)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.CharField(max_length=45, blank=True)  # Track requesting IP

    class Meta:
        ordering = ['-created_at']
        indexes = [
            # The only lookup there is now: this user's outstanding token.
            # The old Index(fields=['code', 'is_used']) is gone with the
            # plaintext column — a salted hash cannot be searched by value.
            models.Index(fields=['user', 'is_used', 'expires_at']),
        ]

    def is_expired(self):
        from django.utils import timezone
        return timezone.now() > self.expires_at

    def is_valid(self):
        return (
            not self.is_used
            and not self.is_expired()
            and not self.attempts_exhausted()
        )

    def attempts_exhausted(self) -> bool:
        """Out of guesses. This — not `is_used` — is how a burned token is marked.

        Deliberately not implemented by setting `is_used=True`: that would hide
        the row from `verify_reset_code`'s "this user's outstanding token" lookup,
        so the next attempt would fall through to the generic
        "invalid or expired" answer and the user would never be told to request a
        new code. Which is the entire reason the lockout message exists. The row
        stays outstanding, keeps answering with the lockout message until it
        expires, and is superseded the moment a new code is requested.

        `is_valid()` above folds this in, so nothing can treat an exhausted token
        as usable by checking only `is_used`.
        """
        return self.attempt_count >= self.MAX_VERIFY_ATTEMPTS

    def __str__(self):
        # Deliberately does not include the code or its hash: this string ends up
        # in the admin changelist, in log lines and in error pages.
        return f"Reset token for {self.user.email} ({self.created_at:%Y-%m-%d %H:%M})"


