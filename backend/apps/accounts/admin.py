from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.admin.utils import unquote
from django.utils import timezone

from . import services
from .models import User, PasswordResetToken


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('email', 'first_name', 'last_name', 'role', 'is_active', 'deactivated_at', 'force_password_change')
    list_filter = ('role', 'is_active', 'force_password_change')
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)
    # The deactivation record is written by services.deactivate_user, never by
    # hand — editing it here would let the audit stamp disagree with is_active.
    readonly_fields = (
        'temporary_password_created_at', 'last_login', 'date_joined',
        'deactivated_at', 'deactivated_by', 'deactivation_reason',
    )
    actions = [
        'force_password_change_on_next_login', 'clear_force_password_change',
        'deactivate_users', 'reactivate_users',
    ]

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('first_name', 'last_name')}),
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
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'first_name', 'last_name', 'password1', 'password2', 'role', 'is_active'),
        }),
    )

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


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ['user', 'code', 'created_at', 'expires_at', 'is_used', 'used_at']
    list_filter = ['is_used', 'created_at']
    search_fields = ['user__email', 'code']
    raw_id_fields = ['user']
    readonly_fields = ['created_at', 'used_at']
    actions = ['invalidate_tokens']

    @admin.action(description="Invalidate selected reset tokens")
    def invalidate_tokens(self, request, queryset):
        updated = queryset.filter(is_used=False).update(is_used=True, used_at=timezone.now())
        self.message_user(request, f"Invalidated {updated} reset token(s).")
