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

Developer accounts
------------------
This admin is where an admin would demote, rename, re-password, deactivate or
delete the platform's owner, because ``role=admin`` implies ``is_superuser`` and
Django hands a superuser every permission unconditionally. Every one of those
five paths is closed below for accounts in ``PLATFORM_DEVELOPER_EMAILS``:

  * ``has_change_permission`` / ``has_delete_permission`` return False for the
    row, which makes Django render it **read-only** rather than hiding it — the
    account stays visible in the changelist and its detail page still opens,
    which is what makes the refusal legible instead of mysterious.
  * ``user_change_password`` refuses, closing the take-over-by-password-reset
    path that the read-only form alone would leave open.
  * every bulk action filters the protected rows out and says so.
  * the ``role`` dropdown drops "Developer" for non-developers, so the role
    cannot be granted here either.

None of it is the primary control — ``User.save()`` and the ``pre_delete``
signal are, and they cover paths this file has never heard of. These guards
exist so an admin gets a clear message instead of a write that silently does
nothing. See apps/accounts/developers.py.
"""

from django import forms
from django.contrib import admin
from django.contrib.admin.utils import quote, unquote
from django.contrib.auth.admin import UserAdmin
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from . import developers, login_guard, services
from .models import PasswordResetToken, User, UserRole

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


def _restrict_form(form_class):
    """The user form as a non-developer may use it. See ``get_form`` for why.

    Restrictions are applied to ``self.fields`` inside ``__init__``, never to
    the class's ``base_fields``. ``base_fields`` is class-level state, and while
    ``modelform_factory`` does hand back a fresh class per request today, some of
    its field instances are shared with the parent form when a field is
    *declared* on it rather than derived from the model. Narrowing a shared
    instance would leak the restriction into a developer's own form. ``fields``
    is deep-copied per form instance, so it cannot.
    """

    class RestrictedUserForm(form_class):
        def __init__(self, *args, **form_kwargs):
            super().__init__(*args, **form_kwargs)
            role = self.fields.get("role")
            if role is not None:
                role.choices = [
                    (value, label) for value, label in UserRole.choices
                    if value != UserRole.DEVELOPER
                ]

        def clean_email(self):
            value = self.cleaned_data.get("email")
            if developers.is_developer_email(value):
                raise forms.ValidationError(
                    "This address belongs to a protected developer account. It "
                    "is configured in PLATFORM_DEVELOPER_EMAILS and cannot be "
                    "assigned here."
                )
            return value

    return RestrictedUserForm


# ══════════════════════════════════════════════════════════════════════════════
# The account
# ══════════════════════════════════════════════════════════════════════════════

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = (
        'email', 'first_name', 'last_name', 'role', 'is_protected', 'is_active',
        'deactivated_at', 'force_password_change', 'login_status',
        'receives_inquiry_alerts', 'timezone',
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

    # ══════════════════════════════════════════════════════════════════════════
    # Developer protection
    #
    # Five separate doors, because Django's admin offers five separate ways to
    # take an account over and closing four of them is the same as closing none:
    #   1. edit the row            -> has_change_permission
    #   2. delete the row          -> has_delete_permission + delete_queryset
    #   3. set its password        -> user_change_password
    #   4. run an action on it     -> _split_protected, used by every action
    #   5. grant the role          -> get_form (role choices)
    # See the module docstring and apps/accounts/developers.py.
    # ══════════════════════════════════════════════════════════════════════════

    @admin.display(description="Protected", boolean=True)
    def is_protected(self, obj):
        """Shown in the changelist so the read-only rows are self-explaining.

        Without a column, an admin meets this feature as "the save button did
        nothing" on a detail page. With one, the row says why before they click.
        """
        return obj.is_developer

    def has_change_permission(self, request, obj=None):
        """Read-only, not hidden, for a protected account.

        Returning False here is what Django turns into a rendered-but-disabled
        form: the changelist link still works and every field displays, because
        superusers retain the separate *view* permission. That is deliberate —
        an admin investigating a support issue can still read the account, they
        simply cannot alter it.

        ``obj=None`` is the changelist and the add form; those must stay
        permitted or the whole User admin disappears for everyone.
        """
        if obj is not None and not developers.can_manage(request.user, obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        """No admin deletes a developer. The ``pre_delete`` signal in
        apps/accounts/signals.py is the real stop; this is what removes the
        button so nobody discovers it by hitting a 500 on the confirmation page.
        """
        if obj is not None and not developers.can_manage(request.user, obj):
            return False
        return super().has_delete_permission(request, obj)

    def get_form(self, request, obj=None, **kwargs):
        """Two restrictions for a non-developer, both on the same form.

        **1. "Developer" leaves the role dropdown.** The privilege does not come
        from this column (developers.py explains why), so selecting it would
        grant nothing — but it would write a row that *claims* a role it does
        not hold, and an operator reading the changelist cannot tell that apart
        from the real thing. A field that cannot mean what it says should not be
        offerable.

        **2. A protected address cannot be typed into `email`.** This closes the
        add form, which ``has_change_permission`` does not cover because on an
        add there is no object yet to refuse. Left open it is a full takeover:
        an admin creates an account at the developer's address with a password
        they choose, ``User.save()`` sees the address in the env list and
        promotes it, and they hold developer credentials.

        In practice ``email`` is ``unique=True`` and ``ensure_developer`` runs in
        the release phase, so the real account almost always exists already and
        the form fails on uniqueness first. "Almost always" is the wrong
        standard for the one control the whole feature rests on — the window
        after a database restore and before the next deploy is exactly when
        someone would try.
        """
        form = super().get_form(request, obj, **kwargs)
        if developers.is_developer(request.user):
            return form
        return _restrict_form(form)

    def user_change_password(self, request, id, form_url=""):
        """Refuse a password change against a protected account.

        The single most important of these guards. A read-only change form still
        leaves Django's separate change-password view reachable by URL, and
        setting someone's password is a complete account takeover — strictly
        worse than deleting them, because it is silent.
        """
        user = self.get_object(request, unquote(id))
        if user is not None and not developers.can_manage(request.user, user):
            self.message_user(request, developers.PROTECTED_MESSAGE, level="error")
            return HttpResponseRedirect(
                reverse("admin:accounts_user_change", args=[quote(user.pk)])
            )
        return self._user_change_password(request, id, form_url)

    def delete_queryset(self, request, queryset):
        """The bulk "Delete selected" action, which does NOT consult
        ``has_delete_permission`` per object — it checks it once with
        ``obj=None`` and then deletes the lot. Filtering here is the only place
        that stops a protected row being swept up alongside others.
        """
        protected, deletable = self._split_protected(request, queryset)
        if protected:
            self.message_user(
                request,
                f"Skipped {len(protected)} protected developer account(s). "
                + developers.PROTECTED_MESSAGE,
                level="error",
            )
        super().delete_queryset(request, deletable)

    def _split_protected(self, request, queryset):
        """(protected rows, the rest) — the shape every action below needs.

        Returns the protected side as a list because callers count it and report
        it; the permitted side stays a queryset so ``.update()`` still works.
        """
        blocked = developers.protected_queryset(queryset, request.user)
        protected = list(blocked)
        if not protected:
            return [], queryset
        return protected, queryset.exclude(pk__in=[u.pk for u in protected])

    def _warn_protected(self, request, protected, verb):
        if protected:
            self.message_user(
                request,
                f"Skipped {len(protected)} protected developer account(s) — "
                f"they cannot be {verb} by anyone but a developer.",
                level="warning",
            )

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
        # Deliberately NOT filtered for protected accounts, unlike every other
        # action here. A login lock is the anti-guessing counter, not a
        # privilege, and a developer can be locked out by five wrong passwords
        # like anyone else — refusing to release it would turn the protection
        # into the lockout it exists to prevent. Releasing a lock only ever
        # *restores* access, so there is nothing here for an admin to abuse.
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
        # Filtered: this is a denial-of-service against a protected account.
        # It cannot steal the account, but it can stand between the developer
        # and their own platform at the moment they most need in — and unlike
        # the other actions it uses queryset.update(), which bypasses
        # User.save() entirely, so nothing downstream would undo it.
        protected, queryset = self._split_protected(request, queryset)
        self._warn_protected(request, protected, "forced to change their password")
        updated = queryset.update(force_password_change=True, temporary_password_created_at=timezone.now())
        self.message_user(request, f"{updated} user(s) will be required to change their password on next login.")

    @admin.action(description="Clear forced password change flag")
    def clear_force_password_change(self, request, queryset):
        # Filtered for consistency, though this one only ever removes a
        # requirement. Leaving one action in the pair unguarded is how the pair
        # drifts apart later.
        protected, queryset = self._split_protected(request, queryset)
        self._warn_protected(request, protected, "modified")
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
        protected, queryset = self._split_protected(request, queryset)
        self._warn_protected(request, protected, "deactivated")

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
        blacklisted refresh tokens stay revoked — the user logs in again.

        Unfiltered, like "Release login lock" and for the same reason: it only
        ever grants access back. Run against a developer it is a no-op, because
        they were never deactivated in the first place.
        """
        reactivated = sum(int(services.reactivate_user(u, by=request.user)["changed"]) for u in queryset)
        self.message_user(request, f"Re-activated {reactivated} user(s). They can log in again.")

    def _user_change_password(self, request, id, form_url=""):
        """The real password-change view. Renamed from ``user_change_password``
        so the protection guard above can own that name and delegate here once
        the target is confirmed editable — keeping the two concerns (may you?
        and then what happens) in separate methods.
        """
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
    # (protection overrides are below the field config — see _protected_tokens)
    list_filter = ['is_used', 'created_at']
    search_fields = ['user__email']
    raw_id_fields = ['user']
    # code_hash is excluded outright rather than made read-only: it is of no use
    # to a human and printing it invites someone to try cracking it offline.
    exclude = ['code_hash']
    readonly_fields = ['created_at', 'used_at', 'attempt_count']
    actions = ['invalidate_tokens']

    # ── Developer protection ─────────────────────────────────────────────────
    #
    # A developer's reset tokens are protected for one specific reason: the
    # password reset IS their recovery path. A developer locked out by five wrong
    # passwords (ADR-0002 — the lock applies to them like anyone else, and
    # should) gets back in by requesting a code. An admin who could burn each
    # code as it was issued would hold them out for the whole 24-hour lock
    # window, which is the closest thing to a real lockout left in the design.
    #
    # Not theoretical enough to skip: it is two clicks, and it is the exact move
    # someone would make after discovering that every other door is shut.

    def _protected_tokens(self, request, queryset):
        """Tokens belonging to a developer, when the actor is not one."""
        if developers.is_developer(request.user):
            return queryset.none()
        emails = developers.developer_emails()
        if not emails:
            return queryset.none()
        match = Q()
        for email in emails:
            match |= Q(user__email__iexact=email)
        return queryset.filter(match)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and not developers.can_manage(request.user, obj.user):
            return False
        return super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        """Bulk delete checks has_delete_permission once with obj=None, never
        per row — so the filtering has to happen here as well."""
        protected = self._protected_tokens(request, queryset)
        count = protected.count()
        if count:
            self.message_user(
                request,
                f"Skipped {count} reset token(s) belonging to a protected "
                "developer account — those codes are that account's recovery path.",
                level="error",
            )
            queryset = queryset.exclude(pk__in=protected.values("pk"))
        super().delete_queryset(request, queryset)

    @admin.action(description="Invalidate selected reset tokens")
    def invalidate_tokens(self, request, queryset):
        protected = self._protected_tokens(request, queryset)
        count = protected.count()
        if count:
            self.message_user(
                request,
                f"Skipped {count} reset token(s) belonging to a protected "
                "developer account — those codes are that account's recovery path.",
                level="warning",
            )
            queryset = queryset.exclude(pk__in=protected.values("pk"))

        updated = queryset.filter(is_used=False).update(is_used=True, used_at=timezone.now())
        self.message_user(request, f"Invalidated {updated} reset token(s).")
