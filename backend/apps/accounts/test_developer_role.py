"""
apps/accounts/test_developer_role.py

The protected `developer` role (apps/accounts/developers.py).

Every test here is an attack from the perspective of a hostile or careless
`role=admin` account — the frontend contractor's login — trying to take the
platform's developer off the platform. Each one names the specific door it is
rattling, because the value of this file is that a future change which reopens
one of them fails here rather than in production, on the day nobody can get in.

``PLATFORM_DEVELOPER_EMAILS`` is set with @override_settings rather than read
from the environment, so the suite is independent of whoever's laptop it runs
on. ``developers.developer_emails()`` deliberately does not cache, which is what
makes that work.
"""

from io import StringIO

from django.conf import settings
from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.cache import cache
from django.core.management import call_command
from django.db import transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from apps.accounts import developers, services
from apps.accounts.models import PasswordResetToken, UserRole
from apps.accounts.signals import ProtectedAccountError
from apps.accounts.utils import create_password_reset_token

User = get_user_model()

DEV_EMAIL = "dev@hephzibahluxe.test"

# The admin sign-in form renders through {% static %}, and the project's
# staticfiles backend is WhiteNoise's manifest storage, which needs a
# collectstatic run to resolve a name. Swapped for the plain backend on the one
# test that posts to /admin/login/, so a FAILED assertion there reports the
# assertion rather than a missing manifest entry. Mirrors what
# AdminLoginIsGuardedTests does in apps/accounts/tests.py.
_PLAIN_STATIC = {
    **settings.STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(PLATFORM_DEVELOPER_EMAILS=[DEV_EMAIL])
class DeveloperRoleBasicsTests(TestCase):
    """The role itself: what it grants, and where it comes from."""

    def setUp(self):
        self.dev = User.objects.create_user(
            first_name="Tobi", last_name="Ojulari",
            email=DEV_EMAIL, password="Sw0rdfish!23",
        )

    def test_a_developer_gets_staff_and_superuser(self):
        """The reason no other app needed changing: every existing
        is_staff/is_superuser check already admits a developer."""
        self.assertEqual(self.dev.role, UserRole.DEVELOPER)
        self.assertTrue(self.dev.is_staff)
        self.assertTrue(self.dev.is_superuser)

    def test_the_role_comes_from_the_env_not_the_argument(self):
        """create_user() was told `client`. The env list overrules it.

        This is the property the whole design rests on — role is derived, not
        stored — so it is asserted directly rather than inferred from behaviour.
        """
        user = User.objects.create_user(
            first_name="Same", last_name="Person",
            email="dev2@hephzibahluxe.test", password="Sw0rdfish!23",
            role=UserRole.CLIENT,
        )
        self.assertEqual(user.role, UserRole.CLIENT)  # not in the env list

        with override_settings(PLATFORM_DEVELOPER_EMAILS=["dev2@hephzibahluxe.test"]):
            user.save()
            self.assertEqual(user.role, UserRole.DEVELOPER)

    def test_the_column_is_a_mirror_and_loses_to_the_env(self):
        """A row claiming role=developer without being in the env list holds no
        privilege. Otherwise a DB write would be an escalation path."""
        impostor = User.objects.create_user(
            first_name="Not", last_name="Me",
            email="impostor@example.com", password="Sw0rdfish!23",
        )
        User.objects.filter(pk=impostor.pk).update(role=UserRole.DEVELOPER)
        impostor.refresh_from_db()

        self.assertEqual(impostor.role, UserRole.DEVELOPER)
        self.assertFalse(impostor.is_developer)
        self.assertFalse(developers.is_developer(impostor))

    def test_matching_is_case_insensitive(self):
        """User.email stores what was typed; the env var is lowercased at boot."""
        self.assertTrue(developers.is_developer_email(DEV_EMAIL.upper()))
        self.assertTrue(developers.is_developer_email(f"  {DEV_EMAIL}  "))

    @override_settings(PLATFORM_DEVELOPER_EMAILS=[])
    def test_an_empty_list_protects_nobody(self):
        """"Unset" must mean "no protected accounts", never "everyone"."""
        self.assertFalse(developers.is_developer(self.dev))
        self.assertTrue(developers.can_manage(self.dev, self.dev))

    def test_anonymous_and_none_are_never_developers(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(developers.is_developer(None))
        self.assertFalse(developers.is_developer(AnonymousUser()))


@override_settings(PLATFORM_DEVELOPER_EMAILS=[DEV_EMAIL])
class DeveloperCannotBeDemotedTests(TestCase):
    """Self-repair: the row loses every argument with the environment."""

    def setUp(self):
        self.dev = User.objects.create_user(
            first_name="Tobi", last_name="Ojulari",
            email=DEV_EMAIL, password="Sw0rdfish!23",
        )

    def test_saving_a_demotion_puts_it_back(self):
        self.dev.role = UserRole.CLIENT
        self.dev.is_active = False
        self.dev.is_superuser = False
        self.dev.save()

        self.dev.refresh_from_db()
        self.assertEqual(self.dev.role, UserRole.DEVELOPER)
        self.assertTrue(self.dev.is_active)
        self.assertTrue(self.dev.is_superuser)

    def test_a_narrow_update_fields_save_cannot_smuggle_a_demotion(self):
        """update_fields is an allow-list, so the correction has to be added to
        it or it is silently dropped on the way to the database. This is the
        exact bug that would make the guard look like it worked."""
        self.dev.is_active = False
        self.dev.save(update_fields=["is_active"])

        self.dev.refresh_from_db()
        self.assertTrue(self.dev.is_active)

    def test_a_queryset_update_is_repaired_at_next_login(self):
        """queryset.update() bypasses save() entirely — the one demotion path
        the model cannot see. Django's auth backend reads is_active off the row
        before any of our code runs, so this MUST be repaired pre-authentication
        or the developer is locked out."""
        User.objects.filter(pk=self.dev.pk).update(
            is_active=False, role=UserRole.CLIENT, is_superuser=False,
        )

        repaired = developers.repair_by_email(DEV_EMAIL)
        self.assertCountEqual(repaired, ["role", "is_active", "is_superuser"])

        self.dev.refresh_from_db()
        self.assertTrue(self.dev.is_active)
        self.assertEqual(self.dev.role, UserRole.DEVELOPER)

    def test_a_deactivated_developer_can_still_log_in(self):
        """The end-to-end version of the test above, through the real endpoint.

        This is the scenario in one line: the contractor deactivates you, and
        you sign in anyway.
        """
        User.objects.filter(pk=self.dev.pk).update(is_active=False)

        response = self.client.post(
            reverse("token_obtain_pair"),
            data={"email": DEV_EMAIL, "password": "Sw0rdfish!23"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("access", response.json())

    @override_settings(STORAGES=_PLAIN_STATIC)
    def test_a_deactivated_developer_can_still_sign_in_through_the_admin(self):
        """The same rescue, through /admin/login/ instead of the API.

        The two login surfaces call ``developers.repair_by_email`` separately,
        so the test above proves nothing about this one — and this is the path
        that matters more. The admin is where an admin demotes people, so it is
        where a locked-out developer would actually be trying to get back in,
        and Django's ModelBackend reads ``is_active`` off the row before any of
        this project's code would otherwise run.

        Written because deleting that one call from apps/core/admin_login.py
        left all 53 tests in this file passing.
        """
        User.objects.filter(pk=self.dev.pk).update(
            is_active=False, role=UserRole.CLIENT, is_staff=False, is_superuser=False,
        )

        response = self.client.post(
            "/admin/login/",
            data={"username": DEV_EMAIL, "password": "Sw0rdfish!23"},
        )

        # A 302 is the admin signing you in and redirecting; a 200 is the form
        # coming back with "please enter the correct email and password".
        self.assertEqual(response.status_code, 302, response.content)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.dev.pk)

        self.dev.refresh_from_db()
        self.assertTrue(self.dev.is_active)
        self.assertTrue(self.dev.is_staff)
        self.assertEqual(self.dev.role, UserRole.DEVELOPER)

    def test_repair_never_touches_a_non_developer(self):
        """A grant must be impossible. If repair() could promote, the env
        anchor would be pointless."""
        ordinary = User.objects.create_user(
            first_name="Ada", last_name="Obi",
            email="client@example.com", password="Sw0rdfish!23",
        )
        ordinary.is_active = False
        ordinary.save()

        self.assertEqual(developers.repair(ordinary), [])
        ordinary.refresh_from_db()
        self.assertFalse(ordinary.is_active)
        self.assertEqual(ordinary.role, UserRole.CLIENT)


@override_settings(PLATFORM_DEVELOPER_EMAILS=[DEV_EMAIL])
class DeveloperCannotBeRemovedTests(TestCase):
    """The service layer and the delete path."""

    def setUp(self):
        self.dev = User.objects.create_user(
            first_name="Tobi", last_name="Ojulari",
            email=DEV_EMAIL, password="Sw0rdfish!23",
        )
        self.admin = User.objects.create_user(
            first_name="Ade", last_name="Frontend",
            email="ade@example.com", password="Sw0rdfish!23",
            role=UserRole.ADMIN,
        )

    def test_an_admin_cannot_deactivate_a_developer(self):
        with self.assertRaises(PermissionDenied):
            services.deactivate_user(self.dev, by=self.admin, reason="bye")

        self.dev.refresh_from_db()
        self.assertTrue(self.dev.is_active)

    def test_the_refusal_is_explicit_not_a_silent_noop(self):
        """save() would put is_active back anyway. A success message describing
        something that did not happen is worse than a 403."""
        with self.assertRaises(PermissionDenied) as ctx:
            services.deactivate_user(self.dev, by=self.admin)
        self.assertIn("protected developer account", str(ctx.exception))

    def test_a_developer_cannot_deactivate_themselves_either(self):
        """expected_state pins is_active=True unconditionally. An offboarding
        switch the protected person can trip is not protection."""
        with self.assertRaises(PermissionDenied):
            services.deactivate_user(self.dev, by=self.dev)

        self.dev.refresh_from_db()
        self.assertTrue(self.dev.is_active)

    @override_settings(
        PLATFORM_DEVELOPER_EMAILS=[DEV_EMAIL, "colleague@hephzibahluxe.test"]
    )
    def test_one_developer_cannot_deactivate_another(self):
        """Developers are PEERS, not ranked — a second developer can edit the
        first. Deactivation is the exception, and it is refused for every actor
        rather than just for outsiders: save() re-derives is_active=True either
        way, so a permitted call would report changed=True while changing
        nothing. Consistent with deletion, which pre_delete refuses for anyone.
        """
        colleague = User.objects.create_user(
            first_name="Second", last_name="Dev",
            email="colleague@hephzibahluxe.test", password="Sw0rdfish!23",
        )

        with self.assertRaises(PermissionDenied):
            services.deactivate_user(self.dev, by=colleague)

        self.dev.refresh_from_db()
        self.assertTrue(self.dev.is_active)

    @override_settings(
        PLATFORM_DEVELOPER_EMAILS=[DEV_EMAIL, "colleague@hephzibahluxe.test"]
    )
    def test_developers_can_otherwise_manage_each_other(self):
        """The flip side, so the peer model is explicit rather than accidental:
        a second developer is a full peer for everything except the two things
        nobody can do."""
        colleague = User.objects.create_user(
            first_name="Second", last_name="Dev",
            email="colleague@hephzibahluxe.test", password="Sw0rdfish!23",
        )
        self.assertTrue(developers.can_manage(colleague, self.dev))
        self.assertTrue(developers.can_manage(self.dev, colleague))

    # The inner `transaction.atomic()` in the next two tests is required, and
    # its necessity is itself the point being made. Model.delete() runs inside
    # an atomic block, so raising from pre_delete marks the transaction broken —
    # no further query may run until it unwinds. The savepoint gives the
    # exception something to roll back to so the assertions afterwards can
    # execute. In production this is a 500 and a rolled-back request, which is
    # the intended shape for a guard that should never be reached: the admin
    # removes the delete button long before here.

    def test_deleting_a_developer_is_blocked(self):
        with self.assertRaises(ProtectedAccountError), transaction.atomic():
            self.dev.delete()
        self.assertTrue(User.objects.filter(pk=self.dev.pk).exists())

    def test_a_bulk_delete_containing_a_developer_aborts_entirely(self):
        """All-or-nothing on purpose: a sweep that quietly skipped the protected
        row would report success and leave the operator with a wrong mental
        model of what the delete did."""
        with self.assertRaises(ProtectedAccountError), transaction.atomic():
            User.objects.all().delete()
        self.assertTrue(User.objects.filter(pk=self.dev.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_an_ordinary_account_still_deletes(self):
        """The guard must not become a general ban on deleting users."""
        self.admin.delete()
        self.assertFalse(User.objects.filter(email="ade@example.com").exists())

    def test_the_api_status_endpoint_refuses(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        response = client.patch(
            reverse("set_user_status", args=[DEV_EMAIL]),
            data={"is_active": False, "reason": "locking you out"},
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.content)

        self.dev.refresh_from_db()
        self.assertTrue(self.dev.is_active)


@override_settings(PLATFORM_DEVELOPER_EMAILS=[DEV_EMAIL])
class DeveloperRoleCannotBeGrantedTests(TestCase):
    """The escalation direction: an admin minting themselves a developer."""

    client_class = APIClient

    def setUp(self):
        self.admin = User.objects.create_user(
            first_name="Ade", last_name="Frontend",
            email="ade@example.com", password="Sw0rdfish!23",
            role=UserRole.ADMIN,
        )
        self.client.force_authenticate(user=self.admin)

    def test_registering_a_developer_is_refused(self):
        response = self.client.post(
            reverse("register"),
            data={
                "email": "newdev@example.com", "first_name": "New",
                "last_name": "Dev", "role": "developer",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("role", response.json().get("errors", {}))
        self.assertFalse(User.objects.filter(email="newdev@example.com").exists())

    def test_registering_a_configured_developer_address_is_refused(self):
        """The other half: whatever role is asked for, an admin must not be able
        to create the account that sits at a protected address and receive its
        credentials email."""
        response = self.client.post(
            reverse("register"),
            data={
                "email": DEV_EMAIL, "first_name": "Not",
                "last_name": "You", "role": "client",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("email", response.json().get("errors", {}))

    def test_an_ordinary_registration_still_works(self):
        response = self.client.post(
            reverse("register"),
            data={
                "email": "client@example.com", "first_name": "Ada",
                "last_name": "Obi", "role": "client",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)


@override_settings(PLATFORM_DEVELOPER_EMAILS=[DEV_EMAIL])
class DeveloperIsLockedInTheDjangoAdminTests(TestCase):
    """The admin site, which is where an admin has the most reach."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.dev = User.objects.create_user(
            first_name="Tobi", last_name="Ojulari",
            email=DEV_EMAIL, password="Sw0rdfish!23",
        )
        self.admin = User.objects.create_user(
            first_name="Ade", last_name="Frontend",
            email="ade@example.com", password="Sw0rdfish!23",
            role=UserRole.ADMIN,
        )
        self.model_admin = site._registry[User]
        self.factory = RequestFactory()

    def _request(self, user):
        request = self.factory.post("/admin/accounts/user/")
        request.user = user
        request.session = "session"
        request._messages = FallbackStorage(request)
        return request

    def test_an_admin_cannot_change_a_developer(self):
        request = self._request(self.admin)
        self.assertFalse(self.model_admin.has_change_permission(request, self.dev))
        self.assertTrue(self.model_admin.has_change_permission(request, self.admin))

    def test_the_changelist_itself_stays_open(self):
        """obj=None is the changelist and the add form. Returning False there
        would remove the User admin for everybody — a protection that breaks
        the admin is not shippable."""
        request = self._request(self.admin)
        self.assertTrue(self.model_admin.has_change_permission(request, None))

    def test_an_admin_cannot_delete_a_developer(self):
        request = self._request(self.admin)
        self.assertFalse(self.model_admin.has_delete_permission(request, self.dev))

    def test_a_developer_can_still_manage_their_own_account(self):
        """The protection is against other people, not against yourself."""
        request = self._request(self.dev)
        self.assertTrue(self.model_admin.has_change_permission(request, self.dev))

    def _role_choices(self, actor, obj):
        """What the rendered dropdown actually offers.

        Read off a form INSTANCE, not off ``base_fields``: the restriction is
        applied to ``self.fields`` in ``__init__`` precisely so it cannot touch
        class-level state, so ``base_fields`` is the wrong place to look and a
        test that read it would pass while the user saw something else.
        """
        form_class = self.model_admin.get_form(self._request(actor), obj)
        return [value for value, _ in form_class(instance=obj).fields["role"].choices]

    def test_the_role_dropdown_hides_developer_from_an_admin(self):
        choices = self._role_choices(self.admin, self.admin)
        self.assertNotIn(UserRole.DEVELOPER, choices)
        self.assertIn(UserRole.ADMIN, choices)

    def test_a_developer_still_sees_the_full_role_dropdown(self):
        choices = self._role_choices(self.dev, self.admin)
        self.assertIn(UserRole.DEVELOPER, choices)

    def test_narrowing_one_form_does_not_leak_into_another(self):
        """The reason the restriction lives on the instance. If it were applied
        to a shared field object, building the admin's form first would strip
        the choice from the developer's form too."""
        self._role_choices(self.admin, self.admin)
        self.assertIn(UserRole.DEVELOPER, self._role_choices(self.dev, self.admin))

    def test_the_deactivate_action_skips_a_developer(self):
        request = self._request(self.admin)
        self.model_admin.deactivate_users(request, User.objects.all())

        self.dev.refresh_from_db()
        self.assertTrue(self.dev.is_active)

    def test_the_force_password_change_action_skips_a_developer(self):
        """Uses queryset.update(), so nothing downstream would undo it. A pure
        denial-of-service against the account that most needs to get in."""
        request = self._request(self.admin)
        self.model_admin.force_password_change_on_next_login(request, User.objects.all())

        self.dev.refresh_from_db()
        self.assertFalse(self.dev.force_password_change)

    def test_the_bulk_delete_action_skips_a_developer(self):
        request = self._request(self.admin)
        self.model_admin.delete_queryset(request, User.objects.all())

        self.assertTrue(User.objects.filter(pk=self.dev.pk).exists())

    def test_releasing_a_login_lock_is_deliberately_not_blocked(self):
        """A lock is about guessing, not privilege. Refusing to release one for
        a developer would turn the protection into the lockout it prevents."""
        self.dev.failed_login_count = User.MAX_FAILED_LOGINS
        self.dev.save(update_fields=["failed_login_count"])

        request = self._request(self.admin)
        self.model_admin.release_login_lock(request, User.objects.filter(pk=self.dev.pk))

        self.dev.refresh_from_db()
        self.assertEqual(self.dev.failed_login_count, 0)

    def test_the_password_change_view_refuses(self):
        """The most important door. A read-only change form still leaves
        Django's separate change-password URL reachable, and setting someone's
        password is a silent, complete takeover."""
        request = self.factory.post(
            f"/admin/accounts/user/{self.dev.pk}/password/",
            data={"password1": "Hijacked!23", "password2": "Hijacked!23"},
        )
        request.user = self.admin
        request.session = "session"
        request._messages = FallbackStorage(request)

        response = self.model_admin.user_change_password(request, str(self.dev.pk))

        self.assertEqual(response.status_code, 302)
        self.dev.refresh_from_db()
        self.assertFalse(self.dev.check_password("Hijacked!23"))
        self.assertTrue(self.dev.check_password("Sw0rdfish!23"))

    def test_an_admin_can_still_change_an_ordinary_users_password(self):
        """The guard must not break normal operations."""
        client = User.objects.create_user(
            first_name="Ada", last_name="Obi",
            email="client@example.com", password="Sw0rdfish!23",
        )
        request = self.factory.get(f"/admin/accounts/user/{client.pk}/password/")
        request.user = self.admin
        request.session = "session"
        request._messages = FallbackStorage(request)

        response = self.model_admin.user_change_password(request, str(client.pk))
        # A GET renders the form (200) rather than bouncing (302).
        self.assertEqual(response.status_code, 200)

    def test_the_add_form_refuses_a_protected_address(self):
        """The takeover the add form would otherwise allow: create an account at
        the developer's address with a password you chose, and User.save()
        promotes it for you. Uniqueness normally blocks it, but not in the
        window after a database restore and before the next deploy."""
        request = self._request(self.admin)
        form_class = self.model_admin.get_form(request, None)
        form = form_class(data={
            "email": DEV_EMAIL, "first_name": "Not", "last_name": "You",
            "password1": "Hijacked!23", "password2": "Hijacked!23",
            "role": UserRole.ADMIN, "is_active": True,
        })

        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_a_developer_may_still_use_a_protected_address_on_the_add_form(self):
        request = self._request(self.dev)
        form_class = self.model_admin.get_form(request, None)
        form = form_class(data={
            "email": "second-dev@hephzibahluxe.test", "first_name": "Co",
            "last_name": "Founder", "password1": "Sw0rdfish!23",
            "password2": "Sw0rdfish!23", "role": UserRole.DEVELOPER,
            "is_active": True,
        })

        self.assertTrue(form.is_valid(), form.errors)

    def test_the_add_form_accepts_an_ordinary_address(self):
        """The validator must not become a general ban on creating users."""
        request = self._request(self.admin)
        form_class = self.model_admin.get_form(request, None)
        form = form_class(data={
            "email": "newclient@example.com", "first_name": "Ada",
            "last_name": "Obi", "password1": "Sw0rdfish!23",
            "password2": "Sw0rdfish!23", "role": UserRole.CLIENT,
            "is_active": True,
        })

        self.assertTrue(form.is_valid(), form.errors)

    def test_the_developer_row_stays_visible(self):
        """Visible-and-locked, not hidden: an admin who cannot see why an edit
        fails works around it instead of understanding it."""
        self.assertTrue(self.model_admin.is_protected(self.dev))
        self.assertFalse(self.model_admin.is_protected(self.admin))


@override_settings(PLATFORM_DEVELOPER_EMAILS=[DEV_EMAIL])
class DeveloperRecoveryPathIsProtectedTests(TestCase):
    """The password reset is how a locked-out developer gets back in, so an
    admin must not be able to burn the codes as they are issued."""

    def setUp(self):
        self.dev = User.objects.create_user(
            first_name="Tobi", last_name="Ojulari",
            email=DEV_EMAIL, password="Sw0rdfish!23",
        )
        self.admin = User.objects.create_user(
            first_name="Ade", last_name="Frontend",
            email="ade@example.com", password="Sw0rdfish!23",
            role=UserRole.ADMIN,
        )
        self.model_admin = site._registry[PasswordResetToken]
        self.factory = RequestFactory()

        # create_password_reset_token returns (token, plaintext_code) — the code
        # exists only at this moment and is deliberately unrecoverable.
        self.dev_token, _ = create_password_reset_token(self.dev)
        self.admin_token, _ = create_password_reset_token(self.admin)

    def _request(self, user):
        request = self.factory.post("/admin/accounts/passwordresettoken/")
        request.user = user
        request.session = "session"
        request._messages = FallbackStorage(request)
        return request

    def test_an_admin_cannot_invalidate_a_developers_reset_code(self):
        self.model_admin.invalidate_tokens(
            self._request(self.admin), PasswordResetToken.objects.all()
        )

        self.dev_token.refresh_from_db()
        self.assertFalse(self.dev_token.is_used)

    def test_other_tokens_in_the_same_action_are_still_invalidated(self):
        """The skip must be surgical — an admin invalidating a batch should not
        have the whole action refused because one row was protected."""
        self.model_admin.invalidate_tokens(
            self._request(self.admin), PasswordResetToken.objects.all()
        )

        self.admin_token.refresh_from_db()
        self.assertTrue(self.admin_token.is_used)

    def test_an_admin_cannot_delete_a_developers_reset_code(self):
        self.model_admin.delete_queryset(
            self._request(self.admin), PasswordResetToken.objects.all()
        )

        self.assertTrue(PasswordResetToken.objects.filter(pk=self.dev_token.pk).exists())
        self.assertFalse(PasswordResetToken.objects.filter(pk=self.admin_token.pk).exists())

    def test_a_developer_may_invalidate_their_own(self):
        self.model_admin.invalidate_tokens(
            self._request(self.dev), PasswordResetToken.objects.all()
        )

        self.dev_token.refresh_from_db()
        self.assertTrue(self.dev_token.is_used)


@override_settings(PLATFORM_DEVELOPER_EMAILS=[DEV_EMAIL])
class EnsureDeveloperCommandTests(TestCase):
    """The bootstrap that makes the account survive a fresh database."""

    def _run(self, *args):
        out = StringIO()
        call_command("ensure_developer", *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_it_creates_a_missing_developer(self):
        self._run()

        user = User.objects.get(email=DEV_EMAIL)
        self.assertEqual(user.role, UserRole.DEVELOPER)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.force_password_change)

    def test_it_is_idempotent(self):
        """It runs on every deploy, so a second run must change nothing."""
        self._run()
        created = User.objects.get(email=DEV_EMAIL)

        output = self._run()

        self.assertEqual(User.objects.filter(email=DEV_EMAIL).count(), 1)
        self.assertIn("already correct", output)
        self.assertEqual(User.objects.get(email=DEV_EMAIL).pk, created.pk)

    def test_it_does_not_reset_an_existing_password(self):
        """A version that re-randomised on each deploy would lock the developer
        out on every push — the exact failure this feature exists to prevent."""
        self._run()
        user = User.objects.get(email=DEV_EMAIL)
        user.set_password("MyRealPassword!23")
        user.save()

        self._run()

        user.refresh_from_db()
        self.assertTrue(user.check_password("MyRealPassword!23"))

    def test_it_repairs_a_demoted_account(self):
        self._run()
        User.objects.filter(email=DEV_EMAIL).update(
            role=UserRole.CLIENT, is_active=False, is_superuser=False,
        )

        output = self._run()

        user = User.objects.get(email=DEV_EMAIL)
        self.assertEqual(user.role, UserRole.DEVELOPER)
        self.assertTrue(user.is_active)
        self.assertIn("repaired", output)

    def test_dry_run_writes_nothing(self):
        output = self._run("--dry-run")

        self.assertFalse(User.objects.filter(email=DEV_EMAIL).exists())
        self.assertIn("DRY RUN", output)

    # ── The two operational paths ────────────────────────────────────────────
    # How this feature is actually adopted and extended, pinned as tests because
    # both are things an operator does once, under pressure, from a RUNBOOK.

    def test_an_existing_admin_account_is_promoted_in_place(self):
        """ADOPTION. The developer already has an admin account with real data
        hanging off it — created_by stamps, a password they know. Promotion must
        be in-place: same row, same primary key, same password, no second
        account. Anything else and adopting this feature costs them their login.
        """
        existing = User.objects.create_user(
            first_name="Tobi", last_name="Ojulari",
            email=DEV_EMAIL, password="MyRealPassword!23",
            role=UserRole.ADMIN,
        )
        # Simulate the pre-upgrade world: the row predates the env var, so put
        # `role` back to what it was before PLATFORM_DEVELOPER_EMAILS existed.
        User.objects.filter(pk=existing.pk).update(role=UserRole.ADMIN)

        self._run()

        self.assertEqual(User.objects.filter(email=DEV_EMAIL).count(), 1)
        promoted = User.objects.get(email=DEV_EMAIL)
        self.assertEqual(promoted.pk, existing.pk)
        self.assertEqual(promoted.role, UserRole.DEVELOPER)
        self.assertTrue(promoted.check_password("MyRealPassword!23"))
        self.assertFalse(promoted.force_password_change)

    def test_adding_a_second_address_promotes_that_account_too(self):
        """EXTENSION. Adding a developer is an env change plus a deploy — no
        migration, no admin click, no code edit."""
        self._run()
        colleague = User.objects.create_user(
            first_name="Second", last_name="Dev",
            email="colleague@hephzibahluxe.test", password="Sw0rdfish!23",
            role=UserRole.ADMIN,
        )

        with override_settings(
            PLATFORM_DEVELOPER_EMAILS=[DEV_EMAIL, "colleague@hephzibahluxe.test"]
        ):
            self._run()

            colleague.refresh_from_db()
            self.assertEqual(colleague.role, UserRole.DEVELOPER)
            self.assertTrue(colleague.check_password("Sw0rdfish!23"))
            # And they immediately hold the protection, not just the label.
            self.assertTrue(developers.is_developer(colleague))

    def test_removing_an_address_returns_the_account_to_ordinary_admin(self):
        """The retirement path. Dropping the address from the env is what makes
        the account manageable again — the account is NOT deleted, it just stops
        being protected, so every normal control applies to it once more."""
        self._run()
        former = User.objects.get(email=DEV_EMAIL)

        with override_settings(PLATFORM_DEVELOPER_EMAILS=[]):
            former.refresh_from_db()
            self.assertFalse(developers.is_developer(former))
            self.assertTrue(developers.can_manage(actor=None, target=former))
            # The stale `role` column is the only leftover, and it grants
            # nothing — it is corrected by hand or on the next role edit.
            self.assertTrue(User.objects.filter(email=DEV_EMAIL).exists())

    @override_settings(PLATFORM_DEVELOPER_EMAILS=[])
    def test_an_empty_list_is_not_an_error(self):
        """It runs in the deploy pipeline. Failing on an unset optional variable
        would make this command the cause of an outage."""
        output = self._run()
        self.assertIn("empty", output)
        self.assertEqual(User.objects.count(), 0)
