"""
apps/accounts/test_password_policy.py

The password policy (apps/accounts/validators.py): min 8 characters, not
entirely numeric, one uppercase letter, one special character — and that every
password-setting path enforces it through validate_password.
"""

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.contrib.auth.password_validation import (
    password_validators_help_texts,
    validate_password,
)
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.utils import create_password_reset_token, generate_temporary_password
from apps.accounts.validators import PasswordPolicyValidator

User = get_user_model()

STRONG = "Sw0rdfish!23"


class PasswordPolicyValidatorTests(TestCase):
    def setUp(self):
        self.v = PasswordPolicyValidator()

    def _codes(self, password):
        try:
            self.v.validate(password)
        except ValidationError as e:
            return sorted(err.code for err in e.error_list)
        return []

    def test_each_rule(self):
        cases = {
            "Ab!1234": ["password_too_short"],
            "12345678": ["password_entirely_numeric", "password_no_special", "password_no_uppercase"],
            "lowercase!1": ["password_no_uppercase"],
            "NoSpecial123": ["password_no_special"],
            STRONG: [],
            "Ünïcode-pass": [],  # any uppercase / any non-alphanumeric counts
        }
        for password, codes in cases.items():
            with self.subTest(password=password):
                self.assertEqual(self._codes(password), codes)

    def test_reports_every_missed_rule_at_once(self):
        with self.assertRaises(ValidationError) as ctx:
            self.v.validate("abc")
        self.assertEqual(len(ctx.exception.messages), 3)
        self.assertIn("Password must be at least 8 characters long.", ctx.exception.messages)

    def test_help_text_is_registered(self):
        self.assertTrue(any("uppercase" in t for t in password_validators_help_texts()))

    def test_registered_alongside_the_kept_django_validators(self):
        user = User(email="ada.obi@example.com", first_name="Ada", last_name="Obi")
        with self.assertRaises(ValidationError):
            validate_password("Password1!", user)  # CommonPasswordValidator still on
        with self.assertRaises(ValidationError):
            validate_password("nouppercase!1", user)  # ours
        validate_password(STRONG, user)

    def test_no_duplicate_messages_for_short_passwords(self):
        """MinimumLength/Numeric were replaced, not stacked, so each failure is
        reported once."""
        with self.assertRaises(ValidationError) as ctx:
            validate_password("Ab!1")
        self.assertEqual(sum("short" in m or "at least 8" in m for m in ctx.exception.messages), 1)


class TemporaryPasswordTests(TestCase):
    def test_generated_passwords_always_satisfy_the_policy(self):
        user = User(email="temp.user@example.com", first_name="Temp", last_name="User")
        for _ in range(500):
            password = generate_temporary_password()
            self.assertTrue(12 <= len(password) <= 16)
            validate_password(password, user)

    def test_an_explicit_short_length_is_floored(self):
        validate_password(generate_temporary_password(4))


class PasswordSettingPathsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="policy@example.com", password="legacy",
        )

    def test_existing_weak_passwords_still_work(self):
        """Validators run when a password is SET, never on login."""
        self.assertIsNotNone(authenticate(email="policy@example.com", password="legacy"))

    # force-password-change

    def _force(self, password):
        self.user.force_password_change = True
        self.user.save()
        self.api.force_authenticate(self.user)
        return self.api.post(
            reverse("force_password_change"),
            {"new_password": password, "confirm_password": password}, format="json",
        )

    def test_force_change_rejects_a_weak_password_in_the_envelope(self):
        resp = self._force("lowercase1!")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["code"], "validation_error")
        self.assertEqual(resp.data["detail"], "Invalid password data.")
        self.assertEqual(
            resp.data["errors"]["new_password"],
            ["Password must contain at least one uppercase letter."],
        )

    def test_force_change_compares_against_the_user(self):
        resp = self._force("Policy@example.com")
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(any("similar" in m for m in resp.data["errors"]["new_password"]))

    def test_force_change_accepts_a_strong_password(self):
        self.assertEqual(self._force(STRONG).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(STRONG))

    # password-reset confirm

    def _confirm(self, code, password):
        return self.api.post(
            reverse("password_reset_confirm"),
            {"email": self.user.email, "code": code,
             "new_password": password, "confirm_password": password},
            format="json",
        )

    def test_reset_confirm_runs_the_full_policy(self):
        token, code = create_password_reset_token(self.user)
        for weak, message in (
            ("nouppercase1!", "Password must contain at least one uppercase letter."),
            ("NoSpecial123", "Password must contain at least one special character (e.g. ! @ # $ % * - _)."),
            ("Password1!", "This password is too common."),
        ):
            with self.subTest(weak=weak):
                resp = self._confirm(code, weak)
                self.assertEqual(resp.status_code, 400)
                # Names the real problem, not "Invalid or expired code".
                self.assertEqual(resp.data["detail"], "Invalid password data.")
                self.assertIn(message, resp.data["errors"]["new_password"])

        # The code survived the rejections; a strong password then succeeds.
        token.refresh_from_db()
        self.assertFalse(token.is_used)
        self.assertEqual(self._confirm(code, STRONG).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(STRONG))

    def test_reset_confirm_with_a_bad_code_still_says_so(self):
        create_password_reset_token(self.user)
        resp = self._confirm("000000", STRONG)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["detail"], "Invalid or expired code.")

    # Django admin change-password form (uses the same validators)

    def test_admin_change_password_form_enforces_the_policy(self):
        weak = AdminPasswordChangeForm(self.user, {"password1": "lowercase1!", "password2": "lowercase1!"})
        self.assertFalse(weak.is_valid())
        self.assertIn("Password must contain at least one uppercase letter.", weak.errors["password2"])
        strong = AdminPasswordChangeForm(self.user, {"password1": STRONG, "password2": STRONG})
        self.assertTrue(strong.is_valid(), strong.errors)
