"""
apps/accounts/test_security_audit.py

Regression tests for the accounts findings of the security audit:

* a password reset or a forced password change revokes every refresh token the
  user holds, and force-change hands back a working replacement pair;
* a reset code cannot be spent twice, even by two requests that both passed
  validation;
* plain staff cannot create, deactivate or reactivate staff/admin accounts;
* ``ensure_developer --password`` runs the password policy;
* a password change (reset, force-change, admin) rejects access tokens issued
  under the old password at once (CHECK_REVOKE_TOKEN).
"""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.accounts.models import PasswordResetToken, UserRole
from apps.accounts.utils import create_password_reset_token

User = get_user_model()

STRONG = "Sw0rdfish!23"
STRONGER = "Tr0mbone?Blue"


def _refresh(api: APIClient, refresh: str):
    return api.post(reverse("token_refresh"), {"refresh": refresh}, format="json")


class PasswordResetRevokesSessionsTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.user = User.objects.create_user(
            first_name="Reset", last_name="Target", email="reset-rev@example.com", password=STRONG,
        )

    def _confirm(self, code, password=STRONGER):
        return self.api.post(
            reverse("password_reset_confirm"),
            {"email": self.user.email, "code": code, "new_password": password, "confirm_password": password},
            format="json",
        )

    def test_old_refresh_tokens_are_rejected_after_a_reset(self):
        old_a = str(RefreshToken.for_user(self.user))
        old_b = str(RefreshToken.for_user(self.user))
        _token, code = create_password_reset_token(self.user)

        resp = self._confirm(code)

        self.assertEqual(resp.status_code, 200, resp.data)
        for old in (old_a, old_b):
            r = _refresh(self.api, old)
            self.assertEqual(r.status_code, 401, r.data)

    def test_a_code_cannot_be_spent_twice(self):
        _token, code = create_password_reset_token(self.user)

        first = self._confirm(code)
        second = self._confirm(code, password="An0ther!Pass")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(STRONGER))

    def test_a_request_that_validated_before_the_first_committed_is_refused(self):
        """Simulates the race: both requests pass serializer validation (which
        holds no lock), then the first one commits before the second reaches the
        locked re-check. The second must be refused, not reset the password again."""
        from apps.accounts import views
        from apps.accounts.serializers import PasswordResetConfirmSerializer

        token, code = create_password_reset_token(self.user)
        real_validate = PasswordResetConfirmSerializer.validate

        def validate_then_lose_the_race(serializer, data):
            data = real_validate(serializer, data)
            # The concurrent winner commits here, between validation and the lock.
            PasswordResetToken.objects.filter(pk=token.pk).update(is_used=True)
            return data

        with patch.object(views.PasswordResetConfirmSerializer, "validate", validate_then_lose_the_race):
            resp = self._confirm(code)

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["code"], "validation_error")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(STRONG))


class ForcePasswordChangeRevokesSessionsTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.user = User.objects.create_user(
            first_name="Force", last_name="Target", email="force-rev@example.com", password="Temp0rary!x",
        )
        self.user.force_password_change = True
        self.user.save()

    def _login(self, password="Temp0rary!x"):
        resp = self.api.post(
            reverse("token_obtain_pair"), {"email": self.user.email, "password": password}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data

    def _change(self, access, password=STRONG):
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.api.post(
            reverse("force_password_change"),
            {"new_password": password, "confirm_password": password}, format="json",
        )
        self.api.credentials()
        return resp

    def test_force_change_revokes_old_tokens_and_returns_a_working_pair(self):
        other_device = self._login()
        this_session = self._login()

        resp = self._change(this_session["access"])

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)

        # Every refresh token minted under the temporary password is dead,
        # including the one belonging to the session that made the change.
        for old in (other_device["refresh"], this_session["refresh"]):
            self.assertEqual(_refresh(self.api, old).status_code, 401)

        # The new pair works: the access token authenticates, the refresh rotates.
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
        me = self.api.get(reverse("user_info"))
        self.api.credentials()
        self.assertEqual(me.status_code, 200, me.data)
        self.assertEqual(me.data["email"], self.user.email)
        self.assertEqual(_refresh(self.api, resp.data["refresh"]).status_code, 200)

    def test_a_rejected_change_revokes_nothing(self):
        session = self._login()

        resp = self._change(session["access"], password="weak")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(_refresh(self.api, session["refresh"]).status_code, 200)


class StaffCannotManagePrivilegedAccountsTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.superuser = User.objects.create_user(
            first_name="Ad", last_name="Min", email="esc-admin@example.com", password="x", role=UserRole.ADMIN,
        )
        self.staff = User.objects.create_user(
            first_name="St", last_name="Aff", email="esc-staff@example.com", password="x", role=UserRole.STAFF,
        )
        self.other_admin = User.objects.create_user(
            first_name="Ot", last_name="Her", email="esc-admin2@example.com", password="x", role=UserRole.ADMIN,
        )
        self.other_staff = User.objects.create_user(
            first_name="Co", last_name="Worker", email="esc-staff2@example.com", password="x", role=UserRole.STAFF,
        )
        self.client_user = User.objects.create_user(
            first_name="Cl", last_name="Ient", email="esc-client@example.com", password="x",
        )

    def _register(self, actor, role):
        self.api.force_authenticate(actor)
        return self.api.post(
            reverse("register"),
            {"email": f"new-{role}-{actor.pk}@example.com", "first_name": "N", "last_name": "U", "role": role},
            format="json",
        )

    def _set_status(self, actor, target, is_active):
        self.api.force_authenticate(actor)
        return self.api.patch(
            reverse("set_user_status", args=[target.email]), {"is_active": is_active}, format="json",
        )

    def test_staff_cannot_create_an_admin_or_staff(self):
        for role in (UserRole.ADMIN, UserRole.STAFF):
            resp = self._register(self.staff, role)
            self.assertEqual(resp.status_code, 400, (role, resp.data))
            self.assertIn("role", resp.data["errors"])
        self.assertFalse(User.objects.filter(email__startswith="new-").exists())

    def test_staff_can_still_create_a_client(self):
        resp = self._register(self.staff, UserRole.CLIENT)
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_superuser_can_create_admin_and_staff(self):
        for role in (UserRole.ADMIN, UserRole.STAFF):
            resp = self._register(self.superuser, role)
            self.assertEqual(resp.status_code, 201, (role, resp.data))
            self.assertEqual(resp.data["user"]["role"], role)

    def test_staff_cannot_deactivate_an_admin_or_another_staff(self):
        for target in (self.other_admin, self.other_staff):
            resp = self._set_status(self.staff, target, False)
            self.assertEqual(resp.status_code, 403, resp.data)
            self.assertEqual(resp.data["code"], "permission_denied")
            target.refresh_from_db()
            self.assertTrue(target.is_active)

    def test_staff_cannot_reactivate_a_staff_account(self):
        self._set_status(self.superuser, self.other_staff, False)
        resp = self._set_status(self.staff, self.other_staff, True)
        self.assertEqual(resp.status_code, 403)
        self.other_staff.refresh_from_db()
        self.assertFalse(self.other_staff.is_active)

    def test_staff_can_still_manage_clients(self):
        self.assertEqual(self._set_status(self.staff, self.client_user, False).status_code, 200)
        self.assertEqual(self._set_status(self.staff, self.client_user, True).status_code, 200)

    def test_superuser_can_deactivate_and_reactivate_an_admin(self):
        self.assertEqual(self._set_status(self.superuser, self.other_admin, False).status_code, 200)
        self.other_admin.refresh_from_db()
        self.assertFalse(self.other_admin.is_active)
        self.assertEqual(self._set_status(self.superuser, self.other_admin, True).status_code, 200)


DEV_EMAIL = "dev-audit@hephzibahluxe.test"


@override_settings(PLATFORM_DEVELOPER_EMAILS=[DEV_EMAIL])
class EnsureDeveloperPasswordPolicyTests(TestCase):
    def _run(self, *args):
        call_command("ensure_developer", *args, stdout=StringIO(), stderr=StringIO())

    def test_a_weak_password_is_refused_and_nothing_is_written(self):
        with self.assertRaises(CommandError) as ctx:
            self._run("--password", "short")
        self.assertIn("password policy", str(ctx.exception))
        self.assertFalse(User.objects.filter(email=DEV_EMAIL).exists())

    def test_a_strong_password_is_applied(self):
        self._run("--password", STRONG)
        user = User.objects.get(email=DEV_EMAIL)
        self.assertTrue(user.check_password(STRONG))

    def test_the_boot_invocation_without_a_password_still_works(self):
        """The Procfile runs bare `ensure_developer` on every boot."""
        self._run()
        self._run()
        self.assertEqual(User.objects.filter(email=DEV_EMAIL).count(), 1)


class PasswordChangeRevokesAccessTokensTests(TestCase):
    """CHECK_REVOKE_TOKEN: an ACCESS token stops working the moment the
    password changes, not when it expires. Blacklisting only covers refresh
    tokens; this covers the access token already in a browser."""

    def setUp(self):
        self.api = APIClient()
        self.user = User.objects.create_user(
            first_name="Revoke", last_name="Target", email="revoke-access@example.com", password=STRONG,
        )

    def _login(self, password=STRONG):
        resp = self.api.post(
            reverse("token_obtain_pair"), {"email": self.user.email, "password": password}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data

    def _me(self, access):
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.api.get(reverse("user_info"))
        self.api.credentials()
        return resp

    def test_login_me_refresh_me_still_works(self):
        pair = self._login()
        self.assertEqual(self._me(pair["access"]).status_code, 200)

        refreshed = _refresh(self.api, pair["refresh"])
        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        self.assertIn("hash_password", AccessToken(refreshed.data["access"]).payload)
        self.assertEqual(self._me(refreshed.data["access"]).status_code, 200)

    def test_reset_confirm_rejects_a_previously_issued_access_token(self):
        access = self._login()["access"]
        self.assertEqual(self._me(access).status_code, 200)
        _token, code = create_password_reset_token(self.user)

        resp = self.api.post(
            reverse("password_reset_confirm"),
            {"email": self.user.email, "code": code, "new_password": STRONGER, "confirm_password": STRONGER},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self._me(access).status_code, 401)

    def test_force_change_rejects_the_old_access_token_and_the_new_one_works(self):
        self.user.force_password_change = True
        self.user.save()
        old_access = self._login()["access"]

        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {old_access}")
        resp = self.api.post(
            reverse("force_password_change"),
            {"new_password": STRONGER, "confirm_password": STRONGER}, format="json",
        )
        self.api.credentials()

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self._me(old_access).status_code, 401)
        self.assertEqual(self._me(resp.data["access"]).status_code, 200)

    def test_admin_password_change_rejects_a_previously_issued_access_token(self):
        access = self._login()["access"]
        admin_user = User.objects.create_superuser(
            first_name="Ad", last_name="Min", email="revoke-admin@example.com", password=STRONG,
        )
        self.client.force_login(admin_user)

        resp = self.client.post(
            reverse("admin:auth_user_password_change", args=[self.user.pk]),
            {"password1": STRONGER, "password2": STRONGER},
        )

        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(STRONGER))
        self.assertEqual(self._me(access).status_code, 401)

    def test_a_token_without_the_claim_gets_a_clean_401_envelope(self):
        """Tokens minted before CHECK_REVOKE_TOKEN was enabled carry no claim."""
        legacy = AccessToken()
        legacy["user_id"] = str(self.user.pk)
        self.assertNotIn("hash_password", legacy.payload)

        resp = self._me(str(legacy))

        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["code"], "password_changed")
        self.assertIn("detail", resp.json())

    def test_a_legacy_refresh_token_refreshes_but_its_access_token_is_still_refused(self):
        """The refresh endpoint does not check the claim, so a pre-deploy refresh
        token still rotates; the access token it yields inherits the missing
        claim and is refused. The frontend then clears the session."""
        legacy = RefreshToken()
        legacy["user_id"] = str(self.user.pk)

        refreshed = _refresh(self.api, str(legacy))

        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        self.assertNotIn("hash_password", AccessToken(refreshed.data["access"]).payload)
        self.assertEqual(self._me(refreshed.data["access"]).status_code, 401)
