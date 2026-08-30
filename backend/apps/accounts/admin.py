"""
apps/accounts/admin.py

Grouped by the thing being operated on, because the login-security controls are
spread across two models and reading them as one surface matters more than
alphabetical tidiness:

  1. CustomUserAdmin        — the account: role, offboarding, and login security
  2. Login-security helpers — the lock filter/column/actions used by (1)
  3. PasswordResetTokenAdmin — the other half of the same story: the reset codes
                               a locked-out user recovers with

Anything that limits or unblocks a *sign-in* is reachable from here. The one
deliberate exception is the IP-keyed tiers (`auth_login`, `auth_login_daily`) —
those describe a machine rather than an account, so there is no user row to hang
them on and clearing them from one would unblock everyone behind that address.
See docs/adr/0002-login-failure-tracking.md.
"""

from django.contrib import admin
from django.contrib.admin.utils import unquote
from django.contrib.auth.admin import UserAdmin
from django.db.models import Q
from django.utils import timezone
from django.utils.html import format_html

from . import login_guard, services
from .models import PasswordResetToken, User

# ══════════════════════════════════════════════════════════════════════════════
# Login security — the filter and helpers CustomUserAdmin below wires in
# ══════════════════════════════════════════════════════════════════════════════

class LoginLockedFilter(admin.SimpleListFilter):
    """Filter the changelist by whether an account is currently locked out.

    Evaluated in SQL rather than by calling ``User.login_locked()`` per row: the
    method is the authority for a single account, but a filter has to be a
    queryset. The two conditions are kept identical on purpose — count at or
    above the ceiling AND a failure recent enough that the run has not aged out.
    """

    title = "login lock"
    parameter_name = "login_lock"

    def lookups(self, request, model_admin):
        return (("locked", "Locked out"), ("ok", "Not locked"))

    def queryset(self, request, queryset):
        cutoff = timezone.now() - User.FAILED_LOGIN_WINDOW
        locked = Q(
            failed_login_count__gte=User.MAX_FAILED_LOGINS,
            failed_login_at__gt=cutoff,
        )
        if self.value() == "locked":
            return queryset.filter(locked)
        if self.value() == "ok":
            return queryset.exclude(locked)
        return queryset


# ══════════════════════════════════════════════════════════════════════════════
# The account
# ══════════════════════════════════════════════════════════════════════════════

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = (
        'email', 'first_name', 'last_name', 'role', 'is_active', 'deactivated_at',
        'force_password_change', 'login_status', 'receives_inquiry_alerts', 'timezone',
    )
    list_filter = (
        'role', 'is_active', 'force_password_change', LoginLockedFilter,
        'receives_inquiry_alerts', 'timezone',
    )
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)
    # The deactivation record is written by services.deactivate_user, never by
    # hand — editing it here would let the audit stamp disagree with is_active.
    readonly_fields = (
        'temporary_password_created_at', 'last_login', 'date_joined',
        'deactivated_at', 'deactivated_by', 'deactivation_reason',
        # Written by the login path, never by hand — typing a count here would
        # let the admin disagree with the cache-side counter that shares the
        # lock decision. Use the "Release login lock" action instead.
        'failed_login_count', 'failed_login_at', 'login_limit_state',
    )
    actions = [
        'force_password_change_on_next_login', 'clear_force_password_change',
        'release_login_lock',
        'deactivate_users', 'reactivate_users',
    ]

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {
            'fields': ('first_name', 'last_name', 'timezone'),
            'description': (
                'Timezone is an IANA name (e.g. <code>Africa/Lagos</code>, '
                '<code>America/New_York</code>); leave it blank to inherit the '
                'platform default. It does not change how timestamps are shown — '
                'it decides which calendar DAY this account\'s payment-due and '
                'meeting-prep digests are computed against, which matters for a '
                'client more than a few hours from UTC. An unknown name is '
                'rejected on save.'
            ),
        }),
        ('Role', {'fields': ('role',)}),
        ('Permissions', {'fields': ('is_active', 'groups', 'user_permissions')}),
        ('Offboarding', {
            'fields': ('deactivated_at', 'deactivated_by', 'deactivation_reason'),
            'description': (
                'Set by the "Deactivate (offboard) selected users" action (or '
                'PATCH /users/&lt;email&gt;/status/). Reverse it with the '
                '"Re-activate" action — that clears these fields and restores login.'
            ),
        }),
        ('Security', {'fields': ('force_password_change', 'temporary_password_created_at')}),
        ('Login security', {
            'fields': ('failed_login_count', 'failed_login_at', 'login_limit_state'),
            'description': (
                'A run of <b>%s</b> consecutive failed sign-ins locks the account; '
                'any successful sign-in resets the run to zero, and an untouched '
                'run ages out after <b>%s</b>. A locked account is refused '
                '<i>before</i> its password is checked — otherwise the ceiling '
                'would bound nothing — so the holder recovers by completing a '
                'password reset (the code goes to their inbox), or by waiting the '
                'window out. Use <b>Release login lock</b> on the changelist to '
                'clear it by hand; it releases the database counter and both '
                'account-keyed rate buckets together, which is the only way to '
                'be sure the next attempt actually gets through.'
                % (User.MAX_FAILED_LOGINS, User.FAILED_LOGIN_WINDOW)
            ),
        }),
        # Tick this to add a staff member to the recipient list for public
        # inquiry alerts. Opt-in and editable — it is deliberately NOT readonly.
        ('Notifications', {'fields': ('receives_inquiry_alerts',)}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'first_name', 'last_name', 'password1', 'password2', 'role', 'is_active'),
        }),
    )

    # ── Login security ───────────────────────────────────────────────────────

    @admin.display(description="Login", ordering="failed_login_count")
    def login_status(self, obj):
        """Locked / failing / OK at a glance, so triage does not need the detail
        page. Reads the model's own `login_locked()` rather than re-deriving the
        rule, so the column can never disagree with what the login view does."""
        if obj.login_locked():
            return format_html(
                '<b style="color:#ba2121">Locked</b> ({}/{})',
                obj.failed_login_count, User.MAX_FAILED_LOGINS,
            )
        if obj.failed_login_count:
            return format_html(
                '<span style="color:#b8860b">{}/{} failed</span>',
                obj.failed_login_count, User.MAX_FAILED_LOGINS,
            )
        return "—"

    @admin.display(description="Rate-limit buckets")
    def login_limit_state(self, obj):
        """What is actually holding this address back, right now.

        The database counter is only half the picture: the two account-keyed rate
        tiers live in the cache and can refuse a sign-in on their own, with a 429
        rather than the reset instruction. Showing them here is what turns "they
        say they still can't get in" from a guess into a reading.
        """
        state = login_guard.account_limit_state(obj.email)
        rows = [
            format_html(
                "<li>{} — <b>{}</b> of {} ({})</li>",
                tier["group"], tier["count"], tier["limit"], tier["rate"],
            )
            for tier in state["tiers"]
        ]
        return format_html(
            "<ul style='margin:0;padding-left:1.1em'>"
            "<li>email failure counter — <b>{}</b> of {}</li>{}</ul>",
            state["failures"], User.MAX_FAILED_LOGINS, format_html("".join(rows)),
        )

    @admin.action(description="Release login lock (unblock sign-in)")
    def release_login_lock(self, request, queryset):
        """Clear everything email-keyed that is refusing these accounts.

        Three separate things can refuse a sign-in for one address and an
        operator cannot see which from the outside, so this clears all of them
        rather than making someone guess: the durable counter on the row, the
        cache counter that mirrors it for addresses with no account, and the two
        account-keyed rate buckets.

        The IP tiers are left alone on purpose — see the module docstring.
        """
        released_accounts = released_buckets = 0
        for user in queryset:
            had_lock = user.failed_login_count or user.failed_login_at
            user.reset_failed_logins()
            freed = login_guard.release_account(user.email)
            released_buckets += freed
            if had_lock or freed:
                released_accounts += 1

        self.message_user(
            request,
            f"Released {released_accounts} account(s) — cleared {released_buckets} "
            "cache bucket(s) plus the stored failure counts. They can sign in "
            "immediately; no password change is required.",
        )

    # ── Password / offboarding ───────────────────────────────────────────────

    @admin.action(description="Force password change on next login")
    def force_password_change_on_next_login(self, request, queryset):
        updated = queryset.update(force_password_change=True, temporary_password_created_at=timezone.now())
        self.message_user(request, f"{updated} user(s) will be required to change their password on next login.")

    @admin.action(description="Clear forced password change flag")
    def clear_force_password_change(self, request, queryset):
        updated = queryset.update(force_password_change=False, temporary_password_created_at=None)
        self.message_user(request, f"Cleared the forced password change flag for {updated} user(s).")

    @admin.action(description="Deactivate (offboard) selected users")
    def deactivate_users(self, request, queryset):
        """
        Offboard without deleting — reversible via the action below.

        Routes through accounts.services.deactivate_user so this behaves
        identically to PATCH /users/<email>/status/ (same token revocation, same
        audit stamp, same self-deactivation guard).
        """
        deactivated = revoked = skipped = 0
        for user in queryset:
            if user.pk == request.user.pk:
                skipped += 1  # can't offboard yourself — see services
                continue
            result = services.deactivate_user(user, by=request.user)
            deactivated += int(result["changed"])
            revoked += result["revoked_tokens"]

        self.message_user(
            request,
            f"Deactivated {deactivated} user(s) and revoked {revoked} refresh token(s). "
            "Their data and history are retained — re-activate to restore access.",
        )
        if skipped:
            self.message_user(request, "Skipped your own account — you can't deactivate yourself.", level="warning")

    @admin.action(description="Re-activate selected users")
    def reactivate_users(self, request, queryset):
        """Restore access and clear the deactivation record. Previously
        blacklisted refresh tokens stay revoked — the user logs in again."""
        reactivated = sum(int(services.reactivate_user(u, by=request.user)["changed"]) for u in queryset)
        self.message_user(request, f"Re-activated {reactivated} user(s). They can log in again.")

    def user_change_password(self, request, id, form_url=""):
        # Django's built-in "Change password" form only touches the password
        # hash, so it never clears force_password_change on its own — do it
        # here whenever the reset actually succeeds (redirect = success).
        response = super().user_change_password(request, id, form_url)
        if request.method == "POST" and response.status_code == 302:
            user = self.get_object(request, unquote(id))
            if user and (user.force_password_change or user.temporary_password_created_at):
                user.force_password_change = False
                user.temporary_password_created_at = None
                user.save(update_fields=["force_password_change", "temporary_password_created_at"])
        return response


# ══════════════════════════════════════════════════════════════════════════════
# The recovery path — the codes a locked-out account signs back in with
# ══════════════════════════════════════════════════════════════════════════════

@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    """
    The reset-code audit trail. Deliberately shows no code and no hash.

    Sits beside the login lock above because it is the other half of one story:
    an account that has run out of sign-in attempts is told to reset, and this is
    where that attempt shows up. `attempt_count` here and `failed_login_count`
    there are the same idea one step apart — five wrong codes burn a token, five
    wrong passwords lock an account.

    This changelist used to print the live six-digit code in a column and let you
    search by it, which made the admin a second way to read a credential that the
    reset email had already delivered to its owner. The code is now stored hashed
    and there is nothing here to display; `attempt_count` is what you actually
    want when triaging "the client says the code doesn't work".
    """
    list_display = [
        'user', 'created_at', 'expires_at', 'attempt_count', 'is_used', 'used_at',
    ]
    list_filter = ['is_used', 'created_at']
    search_fields = ['user__email']
    raw_id_fields = ['user']
    # code_hash is excluded outright rather than made read-only: it is of no use
    # to a human and printing it invites someone to try cracking it offline.
    exclude = ['code_hash']
    readonly_fields = ['created_at', 'used_at', 'attempt_count']
    actions = ['invalidate_tokens']

    @admin.action(description="Invalidate selected reset tokens")
    def invalidate_tokens(self, request, queryset):
        updated = queryset.filter(is_used=False).update(is_used=True, used_at=timezone.now())
        self.message_user(request, f"Invalidated {updated} reset token(s).")
