"""
apps/accounts/tests.py

Rate-limiting behaviour on the public auth endpoints. Throttling is disabled
under the test runner (settings.RATELIMIT_ENABLE = not TESTING), so these opt
back in with @override_settings and clear the shared cache between tests to
reset django-ratelimit's counters.

The test client's REMOTE_ADDR is 127.0.0.1 (loopback = trusted proxy), so
client_ip falls back to it — every request in a test shares one bucket, which
is what lets us drive a bucket to its limit.
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone
from django_ratelimit import core as ratelimit_core
from django_ratelimit.core import _get_window, _split_rate
from rest_framework.test import APIClient

from apps.accounts import login_guard
from apps.accounts.admin import LoginLockedFilter
from apps.accounts.models import PasswordResetToken
from apps.accounts.utils import create_password_reset_token
from apps.core.admin_login import guarded_admin_login
from apps.core.pagination import UserPageNumberPagination

User = get_user_model()


# Read from the configured rate rather than hardcoded, so tuning
# RATE_LIMIT_AUTH_LOGIN in settings.py never silently turns these into tests of
# nothing (a loop of 5 against a 10/m limit still passes — it just stops proving
# the limiter fires). Same approach as apps/inquiries/tests.py.
LOGIN_LIMIT, _ = _split_rate(settings.RATE_LIMITS["auth_login"])

# An arbitrary fixed instant. Only its stability matters, never its value.
_FROZEN_CLOCK = 1_700_000_000.0


def freeze_rate_limit_window(testcase):
    """Pin django-ratelimit's fixed window for the lifetime of `testcase`.

    **Any test that drives a bucket to its exact limit needs this.**
    django-ratelimit's windows are wall-clock aligned, not sliding:
    `core._get_window` returns `ts - (ts % period) + (crc32(key) % period)`, and
    that value is part of the cache key. A burst that straddles an edge therefore
    starts counting again from zero under a NEW key, and the request that should
    have been blocked sails through — surfacing as the flaky
    "AssertionError: 401 != 429".

    It is a real race, not a theoretical one. `auth_login` is 10/m, so the eleven
    requests `test_failures_still_exhaust_the_burst` makes must all land inside
    one 60-second slot. They take ~1.7s locally and several times that on a CI
    runner, because `ModelBackend` runs a full PBKDF2 hash (1,000,000 iterations
    on Django 5.2) even for an address with no account behind it, to equalise
    timing. Seconds of exposure against a 60-second grid, on several tests, every
    run.

    Freezing changes nothing about what is under test — the counters, the keys
    and the tier ordering are all still real. It only stops the clock deciding
    whether the assertion holds.

    Two notes on the mechanism:

    * It patches `time.time` process-wide for the duration, which is safe on
      these paths: `timezone.now()` is datetime-based, so model timestamps and
      token expiry are unaffected, and Redis TTLs are evaluated server-side.
    * `login_guard` does `from django_ratelimit.core import _get_window`, a
      binding this does not reach. That only matters for `release_account` /
      `account_limit_state`, which read a bucket rather than fill one.

    The underlying property is documented as accepted in
    docs/RATE_LIMITING_GUIDE.md §10 — production tolerates a 2x burst at the
    boundary because the controls that need a hard bound count on a row instead.
    A test asserting an exact ceiling has no such tolerance.
    """
    patcher = patch("django_ratelimit.core.time.time", return_value=_FROZEN_CLOCK)
    patcher.start()
    testcase.addCleanup(patcher.stop)


@override_settings(RATELIMIT_ENABLE=True)
class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()  # reset django-ratelimit (and DRF throttle) counters
        freeze_rate_limit_window(self)

    def tearDown(self):
        cache.clear()

    def _post(self, url, **body):
        return self.client.post(url, data=body, content_type="application/json")

    def _post_from(self, client_ip, url, **body):
        """POST as a client at `client_ip`, arriving through the edge proxy.

        REMOTE_ADDR is a private platform address (a trusted hop) and the real
        client is the rightmost XFF entry, which is the shape every request has
        on Railway.
        """
        return self.client.post(
            url, data=body, content_type="application/json",
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_FOR=f"{client_ip}, 10.0.0.5",
        )

    def test_login_blocks_after_limit_with_standard_429_envelope(self):
        url = reverse("token_obtain_pair")
        # A DIFFERENT email each time, deliberately: this is the per-IP tier, and
        # repeating one address would hit the email failure ceiling (5) first and
        # return password_reset_required instead. One machine working through
        # many accounts is exactly the shape this tier is for.
        for attempt in range(LOGIN_LIMIT):
            resp = self._post(url, email=f"x{attempt}@example.com", password="wrong")
            self.assertNotEqual(resp.status_code, 429)

        blocked = self._post(url, email="x-last@example.com", password="wrong")
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["code"], "rate_limited")

    def test_password_reset_is_keyed_by_ip_and_email(self):
        url = reverse("password_reset_request")
        # 3/h per (IP, email): the same email trips on the 4th, but a different
        # email from the same IP is an independent bucket and still passes.
        for _ in range(3):
            resp = self._post(url, email="a@example.com")
            self.assertNotEqual(resp.status_code, 429)

        self.assertEqual(self._post(url, email="a@example.com").status_code, 429)
        self.assertNotEqual(self._post(url, email="b@example.com").status_code, 429)

    def test_password_reset_verify_and_confirm_have_separate_budgets(self):
        """The collision that shipped: both are 10/m on client_ip in one module,
        so with the group left implicit they shared ONE bucket — a user who used
        up their verify attempts could no longer confirm a reset they had already
        verified. Neither endpoint had a rate-limit test, which is why it was
        never noticed."""
        verify, confirm = reverse("password_reset_verify"), reverse("password_reset_confirm")

        # Exhaust verify (10/m). Bad codes 400, which is fine — we only care
        # that the limiter let them through.
        for attempt in range(10):
            resp = self._post(verify, email="a@example.com", code="000000")
            self.assertNotEqual(resp.status_code, 429, f"verify {attempt + 1}")
        self.assertEqual(self._post(verify, email="a@example.com", code="000000").status_code, 429)

        # Confirm must still have its own full allowance.
        resp = self._post(confirm, email="a@example.com", code="000000", new_password="Sw0rdfish!23")
        self.assertNotEqual(resp.status_code, 429)

    def test_login_is_capped_per_account_across_many_ips(self):
        """The axis a per-IP login limit leaves wide open.

        The per-IP tier caps one machine and nothing else: spread the attempts
        over enough source addresses and attempts against a single account are
        unlimited. Driven from a DIFFERENT IP every time, which defeats both IP
        tiers and leaves only the email-keyed defences standing.

        Since ADR-0002 the thing that closes this is the FAILURE COUNTER
        (MAX_FAILED_LOGINS), not the 10/h rate tier — the counter is deliberately
        set lower so a locked account is told to reset rather than being handed
        an opaque 429. The rate tiers remain as backstops above it.
        """
        url = reverse("token_obtain_pair")
        target = "victim@example.com"
        ceiling = get_user_model().MAX_FAILED_LOGINS

        for attempt in range(ceiling):
            resp = self._post_from(f"41.2.3.{attempt}", url, email=target, password="wrong")
            self.assertEqual(resp.status_code, 401, f"attempt {attempt + 1}")

        blocked = self._post_from("41.2.3.99", url, email=target, password="wrong")
        self.assertEqual(blocked.status_code, 401)
        self.assertEqual(blocked.json()["code"], "password_reset_required")

        # A different address from a fresh IP is a different bucket — the cap is
        # on the targeted account, not on the whole endpoint.
        other = self._post_from("41.2.3.98", url, email="someone@example.com", password="wrong")
        self.assertEqual(other.json()["code"], "invalid_credentials")

    def test_a_429_carries_retry_after(self):
        """DRF's throttle has always sent Retry-After; the URL limiter sent
        nothing, so two 429s from one API behaved differently and a client had
        nothing to back off on."""
        url = reverse("token_obtain_pair")
        for attempt in range(LOGIN_LIMIT):
            self._post(url, email=f"x{attempt}@example.com", password="wrong")
        blocked = self._post(url, email="x-last@example.com", password="wrong")
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)
        self.assertGreater(int(blocked.headers["Retry-After"]), 0)

    @override_settings(RATELIMIT_ENABLE=False)
    def test_no_throttling_when_disabled(self):
        url = reverse("token_obtain_pair")
        for _ in range(LOGIN_LIMIT + 3):  # well past the per-IP login limit
            resp = self._post(url, email="x@example.com", password="wrong")
            self.assertNotEqual(resp.status_code, 429)


class DeactivationTests(TestCase):
    """
    Offboarding is a reversible state, not a delete. These lock down the two
    properties that matter: reactivation fully restores the account, and
    deactivation never destroys the user's data or attribution history.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.admin = User.objects.create_user(
            first_name="Win", last_name="A", email="deact-admin@example.com",
            password="x", role="admin",
        )
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="deact-client@example.com", password="x",
        )

    def test_deactivate_records_who_why_and_when(self):
        from apps.accounts import services
        result = services.deactivate_user(self.client_user, by=self.admin, reason="Contract completed")
        self.client_user.refresh_from_db()
        self.assertTrue(result["changed"])
        self.assertFalse(self.client_user.is_active)
        self.assertIsNotNone(self.client_user.deactivated_at)
        self.assertEqual(self.client_user.deactivated_by, self.admin)
        self.assertEqual(self.client_user.deactivation_reason, "Contract completed")

    def test_reactivate_clears_the_record_and_restores_access(self):
        from apps.accounts import services
        services.deactivate_user(self.client_user, by=self.admin, reason="Oops")
        result = services.reactivate_user(self.client_user, by=self.admin)
        self.client_user.refresh_from_db()
        self.assertTrue(result["changed"])
        self.assertTrue(self.client_user.is_active)
        self.assertIsNone(self.client_user.deactivated_at)
        self.assertIsNone(self.client_user.deactivated_by)
        self.assertEqual(self.client_user.deactivation_reason, "")

    def test_deactivation_preserves_the_users_data(self):
        """The whole point of not deleting: the portal and its engagement survive."""
        import datetime

        from apps.accounts import services
        from apps.events.models import Event
        from apps.portal.models import ClientPortal

        portal = ClientPortal.objects.get(user=self.client_user)
        event = Event.objects.create(
            celebrant=self.client_user, title="T", event_type="Wedding",
            bride_name="P", groom_name="S", country="NG", state="Lagos",
            event_date=datetime.date(2027, 1, 1),
        )
        services.deactivate_user(self.client_user, by=self.admin)

        self.assertTrue(ClientPortal.objects.filter(pk=portal.pk).exists())
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())
        self.assertEqual(Event.objects.get(pk=event.pk).celebrant, self.client_user)

    def test_cannot_deactivate_yourself(self):
        from rest_framework.exceptions import ValidationError

        from apps.accounts import services
        with self.assertRaises(ValidationError):
            services.deactivate_user(self.admin, by=self.admin)

    def test_both_operations_are_idempotent(self):
        from apps.accounts import services
        services.deactivate_user(self.client_user, by=self.admin, reason="first")
        again = services.deactivate_user(self.client_user, by=self.admin, reason="second")
        self.client_user.refresh_from_db()
        self.assertFalse(again["changed"])
        # The original reason survives — a repeat call must not overwrite history.
        self.assertEqual(self.client_user.deactivation_reason, "first")

        services.reactivate_user(self.client_user, by=self.admin)
        self.assertFalse(services.reactivate_user(self.client_user, by=self.admin)["changed"])

    def test_status_endpoint_round_trips(self):
        from rest_framework.test import APIRequestFactory, force_authenticate

        from apps.accounts import views

        factory = APIRequestFactory()

        req = factory.patch("/", {"is_active": False, "reason": "Season ended"}, format="json")
        force_authenticate(req, user=self.admin)
        resp = views.set_user_status(req, email=self.client_user.email)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["user"]["is_active"])
        self.assertEqual(resp.data["user"]["deactivation_reason"], "Season ended")
        self.assertEqual(resp.data["user"]["deactivated_by_display"], "Win A")

        req = factory.patch("/", {"is_active": True}, format="json")
        force_authenticate(req, user=self.admin)
        resp = views.set_user_status(req, email=self.client_user.email)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["user"]["is_active"])
        self.assertIsNone(resp.data["user"]["deactivated_at"])


class PasswordResetClientIPTests(TestCase):
    """
    The audit trail on a password reset, and the 500 it used to hide.

    This view resolved the client itself and took the LEFTMOST X-Forwarded-For
    entry — the one the caller supplies — then wrote it, unvalidated, to a
    varchar(45) column.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = get_user_model().objects.create_user(
            email="lead@example.com", password="Sw0rdfish!23",
            first_name="Ada", last_name="Obi",
        )

    def _request_reset(self, email, xff):
        return self.client.post(
            reverse("password_reset_request"),
            data={"email": email}, content_type="application/json",
            REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR=xff,
        )

    def test_audit_records_the_real_client_not_the_supplied_one(self):
        """A caller must not be able to choose what the audit trail says about
        them. The prepended address is what the old leftmost read would have
        stored."""
        resp = self._request_reset(self.user.email, "9.9.9.9, 41.2.3.4, 10.0.0.5")
        self.assertEqual(resp.status_code, 200)

        token = PasswordResetToken.objects.filter(user=self.user).latest("created_at")
        self.assertEqual(token.ip_address, "41.2.3.4")
        self.assertNotEqual(token.ip_address, "9.9.9.9")

    def test_an_overlong_header_does_not_500_and_reveals_nothing(self):
        """The enumeration oracle, closed.

        A >45-character leftmost entry used to reach the varchar(45) column and
        raise DataError. Only DoesNotExist was caught, so the error escaped as a
        500 for an email that EXISTS while an unknown email still returned 200 —
        turning the endpoint's own "always return success to prevent user
        enumeration" into a working yes/no oracle. Both cases must now be
        indistinguishable.
        """
        overlong = "9" * 200 + ", 41.2.3.4, 10.0.0.5"

        known = self._request_reset(self.user.email, overlong)
        unknown = self._request_reset("nobody@example.com", overlong)

        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(known.json(), unknown.json())
        # And the row that was written is still a usable address.
        token = PasswordResetToken.objects.filter(user=self.user).latest("created_at")
        self.assertEqual(token.ip_address, "41.2.3.4")


class UserLookupEnumerationTests(TestCase):
    """
    GET /users/<email>/ must not tell a caller which addresses have accounts.

    Looking the row up and rejecting afterwards makes the status code an oracle:
    404 means "no such account", 403 means "there is one, it just isn't yours".
    Any authenticated caller — including a client with no privileges — could walk
    a candidate list and learn every account in the system.
    """

    # APIClient, because the project authenticates with JWT only — there is no
    # SessionAuthentication, so force_login() would leave DRF seeing an
    # anonymous request.
    client_class = APIClient

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        User = get_user_model()
        self.client_user = User.objects.create_user(
            email="client@example.com", password="Sw0rdfish!23",
            first_name="Cee", last_name="Ell", role="client",
        )
        self.other = User.objects.create_user(
            email="other@example.com", password="Sw0rdfish!23",
            first_name="Oh", last_name="Ther", role="client",
        )
        # is_staff is derived from role on every save(), so set the role.
        self.staff = User.objects.create_user(
            email="staff@example.com", password="Sw0rdfish!23",
            first_name="Es", last_name="Taff", role="staff",
        )

    def _get(self, as_user, email):
        self.client.force_authenticate(user=as_user)
        return self.client.get(reverse("user_info_email", args=[email]))

    def test_another_users_account_is_indistinguishable_from_no_account(self):
        exists = self._get(self.client_user, self.other.email)
        missing = self._get(self.client_user, "ghost@example.com")

        self.assertEqual(exists.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(exists.json(), missing.json())

    def test_legitimate_callers_are_unaffected(self):
        """The collapse must only affect callers who were never entitled to the
        answer: staff, and the user themselves, still read the record."""
        self.assertEqual(self._get(self.staff, self.other.email).status_code, 200)
        self.assertEqual(self._get(self.client_user, self.client_user.email).status_code, 200)


class EmailIsNotSelfServiceTests(TestCase):
    """
    PATCH /users/me/update/ used to accept `email`, which is USERNAME_FIELD.

    A client could rewrite their own login identity with no proof of owning the
    new address and no notice to the old one — so a typo silently redirected
    every future password-reset code to an address they did not control, and the
    lockout surfaced at the next reset, which is when recovery is already needed.
    """

    client_class = APIClient

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        User = get_user_model()
        self.user = User.objects.create_user(
            email="client@example.com", password="Sw0rdfish!23",
            first_name="Cee", last_name="Ell", role="client",
        )
        self.client.force_authenticate(user=self.user)

    def _patch(self, payload):
        return self.client.patch(
            reverse("update_user"), data=payload, format="json",
        )

    def test_a_client_cannot_change_their_own_login_email(self):
        resp = self._patch({"email": "attacker@example.com"})
        self.assertEqual(resp.status_code, 400)

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "client@example.com")

    def test_the_refusal_is_explicit_rather_than_silent(self):
        """read_only_fields alone would DROP the field and return 200 with the
        old address echoed back — the caller believes it worked and discovers
        otherwise at the next password reset. The 400 is the control."""
        resp = self._patch({"email": "attacker@example.com"})
        self.assertIn("email", resp.json().get("errors", {}))

    def test_echoing_the_unchanged_email_back_is_not_refused(self):
        """The natural shape of "load the object, edit one field, PUT it all
        back" includes the current email. That is not an attempted change and
        must not 400, or every full-object update breaks."""
        resp = self._patch({
            "email": "client@example.com", "first_name": "Renamed",
        })
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Renamed")

    def test_case_differences_are_not_treated_as_a_change(self):
        """Emails are compared case-insensitively everywhere else in the
        project (`recipient_email__iexact`, the login lock's lowercasing), so a
        client whose UI upper-cases the address is not attempting anything."""
        resp = self._patch({"email": "Client@Example.com"})
        self.assertEqual(resp.status_code, 200)

    def test_the_fields_a_client_SHOULD_own_still_work(self):
        """The fix must not turn a self-service endpoint into a read-only one —
        timezone in particular is here precisely because the account holder is
        the person who knows the answer."""
        resp = self._patch({
            "first_name": "Ada", "last_name": "Obi", "timezone": "Pacific/Auckland",
        })
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Ada")
        self.assertEqual(self.user.timezone, "Pacific/Auckland")

    def test_the_email_is_still_readable(self):
        """Read-only, not hidden — the UI still shows you your sign-in address."""
        resp = self._patch({"first_name": "Cee"})
        self.assertEqual(resp.json()["email"], "client@example.com")

    def test_privilege_fields_remain_unwritable(self):
        """Not the bug being fixed — role/is_staff/is_superuser were already
        absent from the serializer — but pinned here because this endpoint is
        where a widened field list would do the most damage."""
        resp = self._patch({
            "role": "admin", "is_staff": True, "is_superuser": True,
        })
        self.assertEqual(resp.status_code, 200)  # unknown fields are ignored
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, "client")
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)


class UserDirectoryIsAlwaysBoundedTests(TestCase):
    """
    GET /users/ used to serialise every account on the platform in one response
    — email, name, role, active state, last login, portal id.

    A rate limit caps how many requests a caller makes, not how much each one
    hands over, so a compromised staff token needed exactly one request for the
    whole directory. These pin that the DEFAULT path is bounded, because the
    default is the path an attacker uses.
    """

    client_class = APIClient

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        User = get_user_model()
        self.staff = User.objects.create_user(
            email="staff@example.com", password="Sw0rdfish!23",
            first_name="Es", last_name="Taff", role="staff",
        )
        # Comfortably more than UserPageNumberPagination.page_size (25).
        for n in range(40):
            User.objects.create_user(
                email=f"client{n}@example.com", password="Sw0rdfish!23",
                first_name=f"Cee{n}", last_name="Ell", role="client",
            )
        self.total = User.objects.count()  # 40 clients + the staff account
        self.client.force_authenticate(user=self.staff)

    def _get(self, query=""):
        return self.client.get(reverse("list_users") + query)

    def test_the_default_response_is_paginated(self):
        body = self._get().json()
        self.assertEqual(
            sorted(body.keys()), ["count", "next", "previous", "results"],
        )

    def test_one_request_cannot_take_the_whole_directory(self):
        body = self._get().json()
        self.assertEqual(body["count"], self.total)   # it still SAYS 41
        self.assertEqual(len(body["results"]), 25)    # it does not SEND 41

    def test_count_and_results_still_parse_for_an_existing_caller(self):
        """The envelope only GAINED keys. A caller reading `count` and
        `results` — the shape this endpoint already returned — is not broken by
        the change, it just receives one page."""
        body = self._get().json()
        self.assertIn("count", body)
        self.assertIsInstance(body["results"], list)

    def test_the_rest_is_still_reachable_by_paging(self):
        """Bounded, not truncated. A cap with no way through would be data loss
        dressed up as a security control."""
        seen, page = [], 1
        while True:
            body = self._get(f"?page={page}").json()
            seen.extend(row["email"] for row in body["results"])
            if not body["next"]:
                break
            page += 1
        self.assertEqual(len(seen), self.total)
        self.assertEqual(len(set(seen)), self.total)  # no page overlap or gap

    def test_page_size_is_capped(self):
        """Otherwise `?page_size=100000` is simply the old unbounded response."""
        body = self._get("?page_size=100000").json()
        self.assertLessEqual(
            len(body["results"]), UserPageNumberPagination.max_page_size,
        )

    def test_filters_still_apply_and_are_counted_before_the_page(self):
        """`count` must describe the filtered set, not the page — otherwise the
        UI cannot render "showing 25 of N" for a search."""
        body = self._get("?search=client1").json()
        # client1, client10-client19 -> 11 matches, one page, count agrees.
        self.assertEqual(body["count"], 11)
        self.assertEqual(len(body["results"]), 11)


class MaintenanceTaskTests(TestCase):
    """
    apps/accounts/tasks — both of these are new, and both close a table that only
    ever grew. `flushexpiredtokens` shipped with SimpleJWT and was scheduled
    nowhere; reset tokens were marked used and then kept forever, each one
    holding a plaintext 6-digit code, an IP and the user it belonged to.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            first_name="Prune", last_name="Target",
            email="prune@example.com", password="x",
        )

    def _token(self, days_ago):
        from datetime import timedelta

        from django.utils import timezone

        from apps.accounts.models import PasswordResetToken

        return PasswordResetToken.objects.create(
            user=self.user, code_hash=make_password("123456"),
            expires_at=timezone.now() - timedelta(days=days_ago),
        )

    def test_reset_tokens_past_the_retention_window_are_deleted(self):
        from apps.accounts.models import PasswordResetToken
        from apps.accounts.tasks import RESET_TOKEN_RETENTION_DAYS, prune_expired_reset_tokens

        old = self._token(days_ago=RESET_TOKEN_RETENTION_DAYS + 1)

        prune_expired_reset_tokens()

        self.assertFalse(PasswordResetToken.objects.filter(pk=old.pk).exists())

    def test_a_live_token_is_never_pruned(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.accounts.models import PasswordResetToken
        from apps.accounts.tasks import prune_expired_reset_tokens

        live = PasswordResetToken.objects.create(
            user=self.user, code_hash=make_password("654321"),
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        prune_expired_reset_tokens()

        self.assertTrue(PasswordResetToken.objects.filter(pk=live.pk).exists())

    def test_a_recently_expired_token_is_kept_for_the_audit_trail(self):
        from apps.accounts.models import PasswordResetToken
        from apps.accounts.tasks import prune_expired_reset_tokens

        recent = self._token(days_ago=1)

        prune_expired_reset_tokens()

        self.assertTrue(PasswordResetToken.objects.filter(pk=recent.pk).exists())

    def test_the_admin_kill_switch_stops_the_prune(self):
        from apps.accounts.models import PasswordResetToken
        from apps.accounts.tasks import RESET_TOKEN_RETENTION_DAYS, prune_expired_reset_tokens
        from apps.notifications.models import ScheduledTaskSettings

        ScheduledTaskSettings.objects.update_or_create(
            task_key="accounts_prune_reset_tokens",
            defaults={"label": "prune", "is_enabled": False},
        )
        old = self._token(days_ago=RESET_TOKEN_RETENTION_DAYS + 1)

        prune_expired_reset_tokens()

        self.assertTrue(PasswordResetToken.objects.filter(pk=old.pk).exists())

    def test_flushing_expired_jwts_runs_clean(self):
        """Thin wrapper around a management command — what matters is that the
        command still exists under this SimpleJWT version and the task reaches it
        without raising."""
        from apps.accounts.tasks import flush_expired_jwt_tokens

        result = flush_expired_jwt_tokens.apply()

        self.assertTrue(result.successful(), result.result)


class ResetCodeTTLTests(TestCase):
    """P0-2. The code's lifetime and the number quoted in the email must be the
    same value, and it must comfortably exceed the retry sweep's cadence — a
    failed send is now re-driven only by cron, so a TTL shorter than that ships
    users a code that is already dead."""

    def test_the_ttl_and_the_number_in_the_email_cannot_drift(self):
        from unittest.mock import patch

        from apps.accounts.utils import RESET_CODE_TTL_MINUTES, send_password_reset_email

        user = User.objects.create_user(
            first_name="TTL", last_name="Check", email="ttl@example.com", password="x",
        )
        with patch("apps.notifications.services.queue_notification") as mock_queue:
            send_password_reset_email(user, "424242")

        context = mock_queue.call_args.kwargs["context"]
        self.assertEqual(context["expires_in_minutes"], RESET_CODE_TTL_MINUTES)

    def test_the_token_expiry_uses_the_same_constant(self):
        from apps.accounts.utils import RESET_CODE_TTL_MINUTES, create_password_reset_token

        user = User.objects.create_user(
            first_name="TTL2", last_name="Check", email="ttl2@example.com", password="x",
        )
        token, _code = create_password_reset_token(user)

        minutes = (token.expires_at - token.created_at).total_seconds() / 60
        self.assertAlmostEqual(minutes, RESET_CODE_TTL_MINUTES, delta=1)

    def test_the_ttl_leaves_room_for_at_least_two_sweep_passes(self):
        """The sweep is the only retry path. If the TTL ever drops below its
        cadence, one transient Brevo blip kills the reset outright."""
        from apps.accounts.utils import RESET_CODE_TTL_MINUTES

        sweep_cadence_minutes = 10  # cron-notify job schedule: */10 * * * *
        self.assertGreaterEqual(RESET_CODE_TTL_MINUTES, sweep_cadence_minutes * 2)


class ResetCodeAtRestTests(TestCase):
    """
    P1-2. The six-digit code must not be readable from the database.

    A 10^6 search space is the whole reason the *hasher* matters and a bare
    digest would not: SHA-256 of every six-digit code is a sub-second table.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            first_name="Hash", last_name="Target",
            email="hash@example.com", password="x",
        )

    def test_the_plaintext_code_is_never_stored(self):
        from apps.accounts.utils import create_password_reset_token

        token, code = create_password_reset_token(self.user)

        self.assertNotIn(code, token.code_hash)
        # And there is no column it could be hiding in.
        columns = {f.name for f in PasswordResetToken._meta.get_fields()}
        self.assertNotIn("code", columns)

    def test_the_hash_uses_the_password_hasher_not_a_bare_digest(self):
        """A per-row salt and an iteration count are the point. `pbkdf2_sha256$`
        prefix is Django's; a raw sha256 hexdigest would be 64 bare hex chars."""
        from apps.accounts.utils import create_password_reset_token

        token, _code = create_password_reset_token(self.user)

        self.assertIn("$", token.code_hash)
        self.assertTrue(token.code_hash.startswith("pbkdf2_"))

    def test_two_identical_codes_hash_differently(self):
        """Per-row salt: otherwise the table leaks which users share a code."""
        from django.contrib.auth.hashers import make_password

        self.assertNotEqual(make_password("123456"), make_password("123456"))

    def test_a_correct_code_still_verifies(self):
        from apps.accounts.utils import create_password_reset_token, verify_reset_code

        _token, code = create_password_reset_token(self.user)

        ok, result = verify_reset_code(self.user.email, code)

        self.assertTrue(ok, result)
        self.assertIsInstance(result, PasswordResetToken)

    def test_a_wrong_code_is_rejected(self):
        from apps.accounts.utils import create_password_reset_token, verify_reset_code

        _token, code = create_password_reset_token(self.user)
        wrong = "000000" if code != "000000" else "111111"

        ok, _message = verify_reset_code(self.user.email, wrong)

        self.assertFalse(ok)

    def test_an_expired_code_is_rejected(self):
        from datetime import timedelta

        from django.utils import timezone as dj_timezone

        from apps.accounts.utils import create_password_reset_token, verify_reset_code

        token, code = create_password_reset_token(self.user)
        PasswordResetToken.objects.filter(pk=token.pk).update(
            expires_at=dj_timezone.now() - timedelta(minutes=1)
        )

        ok, message = verify_reset_code(self.user.email, code)

        self.assertFalse(ok)
        self.assertEqual(message, "Code has expired")

    def test_requesting_a_new_code_invalidates_the_previous_one(self):
        """The invariant verify_reset_code relies on: at most one outstanding
        token per user, which is what lets it fetch-then-check instead of
        looking up by a value it can no longer search."""
        from apps.accounts.utils import create_password_reset_token, verify_reset_code

        _first_token, first_code = create_password_reset_token(self.user)
        _second_token, second_code = create_password_reset_token(self.user)

        self.assertFalse(verify_reset_code(self.user.email, first_code)[0])
        self.assertTrue(verify_reset_code(self.user.email, second_code)[0])

    def test_the_admin_shows_no_code_and_no_hash(self):
        from django.contrib import admin

        from apps.accounts.admin import PasswordResetTokenAdmin

        model_admin = PasswordResetTokenAdmin(PasswordResetToken, admin.site)
        self.assertNotIn("code", model_admin.list_display)
        self.assertNotIn("code_hash", model_admin.list_display)
        self.assertIn("code_hash", model_admin.exclude)
        self.assertNotIn("code", model_admin.search_fields)

    def test_str_does_not_leak_the_code_or_hash(self):
        """__str__ lands in the admin changelist, log lines and error pages."""
        from apps.accounts.utils import create_password_reset_token

        token, code = create_password_reset_token(self.user)

        rendered = str(token)
        self.assertNotIn(code, rendered)
        self.assertNotIn(token.code_hash, rendered)


class ResetCodeAttemptLimitTests(TestCase):
    """
    P2-2, and the thing that makes the 30-minute TTL safe. Before this, a code
    was guessable for its whole window with only the per-IP verify limits as a
    ceiling.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            first_name="Attempt", last_name="Target",
            email="attempts@example.com", password="x",
        )

    def _issue(self):
        from apps.accounts.utils import create_password_reset_token
        return create_password_reset_token(self.user)

    def _wrong(self, code):
        return "000000" if code != "000000" else "111111"

    def test_each_wrong_guess_spends_an_attempt(self):
        from apps.accounts.utils import verify_reset_code

        token, code = self._issue()
        verify_reset_code(self.user.email, self._wrong(code))

        token.refresh_from_db()
        self.assertEqual(token.attempt_count, 1)

    def test_the_token_is_burned_after_max_attempts(self):
        from apps.accounts.utils import verify_reset_code

        token, code = self._issue()
        wrong = self._wrong(code)
        for _ in range(PasswordResetToken.MAX_VERIFY_ATTEMPTS):
            verify_reset_code(self.user.email, wrong)

        token.refresh_from_db()
        self.assertEqual(token.attempt_count, PasswordResetToken.MAX_VERIFY_ATTEMPTS)
        self.assertTrue(token.attempts_exhausted())
        self.assertFalse(token.is_valid())
        # Deliberately still `is_used=False` — see attempts_exhausted(). Marking
        # it used would hide the row from the lookup, and the next attempt would
        # get a generic "invalid" instead of "request a new code".
        self.assertFalse(token.is_used)

    def test_the_right_code_no_longer_works_once_burned(self):
        """The whole point: an attacker who guesses correctly on attempt six must
        get nothing."""
        from apps.accounts.utils import verify_reset_code

        _token, code = self._issue()
        wrong = self._wrong(code)
        for _ in range(PasswordResetToken.MAX_VERIFY_ATTEMPTS):
            verify_reset_code(self.user.email, wrong)

        ok, message = verify_reset_code(self.user.email, code)

        self.assertFalse(ok)
        self.assertIn("request a new code", message)

    def test_a_correct_code_within_the_budget_still_works(self):
        from apps.accounts.utils import verify_reset_code

        _token, code = self._issue()
        verify_reset_code(self.user.email, self._wrong(code))

        self.assertTrue(verify_reset_code(self.user.email, code)[0])

    def test_a_correct_guess_does_not_spend_an_attempt(self):
        from apps.accounts.utils import verify_reset_code

        token, code = self._issue()
        verify_reset_code(self.user.email, code)

        token.refresh_from_db()
        self.assertEqual(token.attempt_count, 0)

    def test_the_budget_is_smaller_than_the_search_space_by_a_wide_margin(self):
        """Guard against someone raising this to something meaningless. Five
        guesses against 10^6 is the property; 1000 would not be."""
        self.assertLessEqual(PasswordResetToken.MAX_VERIFY_ATTEMPTS, 10)


@override_settings(RATELIMIT_ENABLE=True)
class LoginCountsFailuresOnlyTests(TestCase):
    """
    P1 of ADR-0002: a CORRECT login must cost nothing.

    The decorator could not express this — it increments before the view knows
    the outcome — so an office behind one NAT spent anti-brute-force budget by
    logging in successfully. The tiers now live in login_guard and are counted
    only after authentication fails.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        freeze_rate_limit_window(self)
        self.password = "Sw0rdfish!23"
        self.user = User.objects.create_user(
            first_name="Ada", last_name="Obi",
            email="ada@example.com", password=self.password,
        )

    def _login(self, password, email="ada@example.com", client_ip="41.2.3.4"):
        # email is overridable so a test can isolate the IP tier with an address
        # that has no account behind it.
        return self.client.post(
            reverse("token_obtain_pair"),
            data={"email": email, "password": password},
            content_type="application/json",
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_FOR=f"{client_ip}, 10.0.0.5",
        )

    def test_successful_logins_never_exhaust_the_per_ip_burst(self):
        """The headline fix. Well past the per-IP limit, all from one address,
        all correct — this is the office-behind-one-NAT case that used to 429."""
        for attempt in range(LOGIN_LIMIT + 5):
            response = self._login(self.password)
            self.assertEqual(response.status_code, 200, f"login {attempt + 1}")

    # A DIFFERENT email per attempt, deliberately. ANY single address — real or
    # invented — hits the email failure ceiling (5) first and returns
    # password_reset_required, so the only way to reach the per-IP tier is to
    # vary the account. Which is what that tier is for: one machine working
    # through many accounts.
    @staticmethod
    def _unknown(attempt):
        return f"nobody{attempt}@example.com"

    def test_failures_still_exhaust_the_burst(self):
        for attempt in range(LOGIN_LIMIT):
            response = self._login("wrong", email=self._unknown(attempt))
            self.assertEqual(response.status_code, 401, f"attempt {attempt + 1}")
        blocked = self._login("wrong", email="nobody-last@example.com")
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["code"], "rate_limited")

    def test_the_429_is_the_project_standard_envelope(self):
        """Raised from INSIDE the view, so it must escape DRF rather than
        becoming the 403 that PermissionDenied normally maps to."""
        for attempt in range(LOGIN_LIMIT):
            self._login("wrong", email=self._unknown(attempt))
        blocked = self._login("wrong", email="nobody-last@example.com")
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)
        self.assertEqual(set(blocked.json()), {"detail", "code"})

    def test_a_success_does_not_refund_the_ip_buckets(self):
        """Reset-on-success clears the counters for ONE address, never the IP
        rate buckets. A machine that guessed wrong nine times has still made nine
        attempts, whoever it succeeded as in between."""
        for attempt in range(LOGIN_LIMIT - 1):
            self._login("wrong", email=self._unknown(attempt))

        # Same IP, correct credentials — costs nothing, and refunds nothing.
        self.assertEqual(self._login(self.password).status_code, 200)

        self.assertEqual(self._login("wrong", email="nobody-a@example.com").status_code, 401)
        self.assertEqual(self._login("wrong", email="nobody-b@example.com").status_code, 429)

    def test_a_malformed_request_is_a_400_and_is_not_counted(self):
        """Not a credential guess. Still covered by the shared anon ceiling."""
        for _ in range(LOGIN_LIMIT + 2):
            response = self.client.post(
                reverse("token_obtain_pair"),
                data={"email": "ada@example.com"},  # no password
                content_type="application/json",
                REMOTE_ADDR="10.0.0.5",
                HTTP_X_FORWARDED_FOR="41.2.3.4, 10.0.0.5",
            )
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self._login(self.password).status_code, 200)


@override_settings(RATELIMIT_ENABLE=True)
class LoginFailureCounterTests(TestCase):
    """
    P2 of ADR-0002: the per-account failure counter, and the two properties that
    keep it from being a lockout weapon.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.password = "Sw0rdfish!23"
        self.user = User.objects.create_user(
            first_name="Ada", last_name="Obi",
            email="ada@example.com", password=self.password,
        )

    def _login(self, password, client_ip="41.2.3.4", email="ada@example.com"):
        return self.client.post(
            reverse("token_obtain_pair"),
            data={"email": email, "password": password},
            content_type="application/json",
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_FOR=f"{client_ip}, 10.0.0.5",
        )

    def _fail_from_many_ips(self, count):
        """Drive failures from a DIFFERENT address each time, which defeats both
        per-IP tiers and leaves only the account-keyed ones standing — the shape
        the whole counter exists for."""
        for attempt in range(count):
            self._login("wrong", client_ip=f"41.2.3.{attempt}")

    def test_failures_accumulate_on_the_account(self):
        self._fail_from_many_ips(3)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_count, 3)
        self.assertIsNotNone(self.user.failed_login_at)

    def test_a_successful_login_clears_the_run(self):
        self._fail_from_many_ips(3)
        self.assertEqual(self._login(self.password, client_ip="41.2.3.90").status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_count, 0)
        self.assertIsNone(self.user.failed_login_at)

    def test_the_account_locks_after_the_ceiling(self):
        self._fail_from_many_ips(User.MAX_FAILED_LOGINS)
        self.user.refresh_from_db()
        self.assertTrue(self.user.login_locked())

        response = self._login("wrong", client_ip="41.2.3.90")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "password_reset_required")

    def test_a_locked_account_is_refused_before_its_password_is_checked(self):
        """It has to be this way round, or the ceiling bounds nothing — and every
        guess would spend ~68ms of PBKDF2. The cost, accepted: a correct password
        cannot rescue a locked account. The reset flow is the door
        (test_completing_a_password_reset_clears_the_lock)."""
        self._fail_from_many_ips(User.MAX_FAILED_LOGINS)
        self.user.refresh_from_db()
        self.assertTrue(self.user.login_locked())

        response = self._login(self.password, client_ip="41.2.3.90")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "password_reset_required")

    def test_a_success_before_the_ceiling_clears_the_run(self):
        """Reset-on-success is what keeps ordinary fumbling away from the
        ceiling: only an unbroken run of failures ever escalates."""
        self._fail_from_many_ips(User.MAX_FAILED_LOGINS - 1)
        self.assertEqual(self._login(self.password, client_ip="41.2.3.90").status_code, 200)

        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_count, 0)

        # And the run genuinely restarts rather than resuming near the ceiling.
        self._fail_from_many_ips(User.MAX_FAILED_LOGINS - 1)
        self.user.refresh_from_db()
        self.assertFalse(self.user.login_locked())

    def test_completing_a_password_reset_clears_the_lock(self):
        """The documented door out, for someone who no longer knows the
        password. The code goes to an inbox the attacker cannot read."""
        self._fail_from_many_ips(User.MAX_FAILED_LOGINS)
        self.user.refresh_from_db()
        self.assertTrue(self.user.login_locked())

        _, code = create_password_reset_token(self.user)
        response = self.client.post(
            reverse("password_reset_confirm"),
            data={
                "email": self.user.email, "code": code,
                "new_password": "N3wPassw0rd!", "confirm_password": "N3wPassw0rd!",
            },
            content_type="application/json",
            REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="41.2.3.91, 10.0.0.5",
        )
        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        self.assertFalse(self.user.login_locked())
        self.assertEqual(self.user.failed_login_count, 0)

    def test_an_old_run_ages_out_without_a_sweep(self):
        """Lazily, on the next attempt — no scheduled job exists to clear these
        and none should (RUNBOOK: nothing wakes Neon just to tidy a counter)."""
        self.user.failed_login_count = User.MAX_FAILED_LOGINS
        self.user.failed_login_at = timezone.now() - User.FAILED_LOGIN_WINDOW - timedelta(minutes=1)
        self.user.save(update_fields=["failed_login_count", "failed_login_at"])

        self.assertFalse(self.user.login_locked())

        # And the next failure restarts the run at 1 rather than continuing it.
        self._login("wrong", client_ip="41.2.3.92")
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_count, 1)

    def test_an_unknown_email_locks_exactly_like_a_real_one(self):
        """The enumeration fix. An address with no account has no row to count
        on, so a lock driven by User.failed_login_count alone would answer "does
        this address have an account here?" for the price of five wrong
        passwords. The email-keyed counter in login_guard makes the two
        indistinguishable."""
        for attempt in range(User.MAX_FAILED_LOGINS):
            response = self._login(
                "wrong", client_ip=f"51.2.3.{attempt}", email="nobody@example.com"
            )
            self.assertEqual(response.status_code, 401, f"attempt {attempt + 1}")

        locked = self._login("wrong", client_ip="51.2.3.90", email="nobody@example.com")
        self.assertEqual(locked.status_code, 401)
        self.assertEqual(locked.json()["code"], "password_reset_required")


@override_settings(RATELIMIT_ENABLE=True)
class LoginDoesNotLeakAccountExistenceTests(TestCase):
    """
    The oracle this closes: before the email-keyed counter existed, five wrong
    passwords told you whether an address had an account, because only a real one
    could reach `password_reset_required`.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            first_name="Ada", last_name="Obi",
            email="real@example.com", password="Sw0rdfish!23",
        )

    def _drive_to_lock(self, email):
        """Fail MAX_FAILED_LOGINS times from a different IP each time, then one
        more, and return that last response."""
        for attempt in range(User.MAX_FAILED_LOGINS):
            self.client.post(
                reverse("token_obtain_pair"),
                data={"email": email, "password": "wrong"},
                content_type="application/json",
                REMOTE_ADDR="10.0.0.5",
                HTTP_X_FORWARDED_FOR=f"61.2.3.{attempt}, 10.0.0.5",
            )
        return self.client.post(
            reverse("token_obtain_pair"),
            data={"email": email, "password": "wrong"},
            content_type="application/json",
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_FOR="61.2.3.99, 10.0.0.5",
        )

    def test_a_real_and_an_invented_address_are_indistinguishable(self):
        real = self._drive_to_lock("real@example.com")
        cache.clear()  # independent run, not a continuation of the first
        invented = self._drive_to_lock("ghost@example.com")

        self.assertEqual(real.status_code, invented.status_code)
        self.assertEqual(real.json(), invented.json())
        self.assertEqual(real.json()["code"], "password_reset_required")

    def test_the_log_still_distinguishes_them(self):
        """An operator must be able to see WHICH real account is under attack —
        the log is not a surface an attacker reads."""
        with self.assertLogs("apps.accounts.views", level="WARNING") as captured:
            self._drive_to_lock("real@example.com")
        self.assertTrue(
            any("login_account_locked" in r.getMessage() or getattr(r, "event", "") == "login_account_locked"
                for r in captured.records)
        )
        record = next(r for r in captured.records if getattr(r, "event", "") == "login_account_locked")
        self.assertTrue(record.has_account)
        self.assertEqual(record.user_id, str(self.user.id))

    def test_a_successful_login_clears_the_email_counter_too(self):
        for attempt in range(User.MAX_FAILED_LOGINS - 1):
            self.client.post(
                reverse("token_obtain_pair"),
                data={"email": "real@example.com", "password": "wrong"},
                content_type="application/json",
                REMOTE_ADDR="10.0.0.5",
                HTTP_X_FORWARDED_FOR=f"62.2.3.{attempt}, 10.0.0.5",
            )
        self.assertGreater(login_guard.email_failure_count("real@example.com"), 0)

        ok = self.client.post(
            reverse("token_obtain_pair"),
            data={"email": "real@example.com", "password": "Sw0rdfish!23"},
            content_type="application/json",
            REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="62.2.3.90, 10.0.0.5",
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(login_guard.email_failure_count("real@example.com"), 0)

    def test_a_completed_reset_clears_the_email_counter_too(self):
        """Otherwise the recovery the user was told to perform would not
        actually let them back in."""
        self._drive_to_lock("real@example.com")
        self.assertTrue(login_guard.email_is_locked("real@example.com"))

        _, code = create_password_reset_token(self.user)
        response = self.client.post(
            reverse("password_reset_confirm"),
            data={
                "email": "real@example.com", "code": code,
                "new_password": "N3wPassw0rd!", "confirm_password": "N3wPassw0rd!",
            },
            content_type="application/json",
            REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="63.2.3.1, 10.0.0.5",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(login_guard.email_is_locked("real@example.com"))

        ok = self.client.post(
            reverse("token_obtain_pair"),
            data={"email": "real@example.com", "password": "N3wPassw0rd!"},
            content_type="application/json",
            REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="63.2.3.2, 10.0.0.5",
        )
        self.assertEqual(ok.status_code, 200)


class LoginAccountDailyBackstopTests(TestCase):
    """§3 of ADR-0002 — the tier that could only be added once the limits
    counted failures and reset on success."""

    def test_the_per_account_day_exists_and_exceeds_the_hour(self):
        hourly, _ = _split_rate(settings.RATE_LIMITS["auth_login_account"])
        daily, _ = _split_rate(settings.RATE_LIMITS["auth_login_account_daily"])
        self.assertGreater(daily, hourly, "a daily cap below the hourly one is dead")

    def test_it_bites_before_the_old_240_a_day_exposure(self):
        """The gap this closes: 10/h with no daily sibling allowed 240 guesses a
        day against one account from unlimited addresses."""
        hourly, _ = _split_rate(settings.RATE_LIMITS["auth_login_account"])
        daily, _ = _split_rate(settings.RATE_LIMITS["auth_login_account_daily"])
        self.assertLess(daily, hourly * 24)

    def test_the_account_lock_binds_before_either_account_tier(self):
        """Ordering that must not drift. Both account-keyed tiers refuse with a
        429 *before* the login view can look at the account, so if the ceiling
        were not the lowest of the three a locked account would report a rate
        limit and the user would never be told to reset — the recovery path would
        be invisible. The counter is the primary control for a REAL account; the
        tiers are the backstop for addresses with no account behind them."""
        hourly, _ = _split_rate(settings.RATE_LIMITS["auth_login_account"])
        daily, _ = _split_rate(settings.RATE_LIMITS["auth_login_account_daily"])
        self.assertLess(User.MAX_FAILED_LOGINS, hourly)
        self.assertLess(User.MAX_FAILED_LOGINS, daily)


class LoginLockAdminTests(TestCase):
    """The admin surface for releasing a locked account (ADR-0002)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.admin = User.objects.create_superuser(
            first_name="Root", last_name="Admin",
            email="root@example.com", password="Sw0rdfish!23",
        )
        self.locked = User.objects.create_user(
            first_name="Ada", last_name="Obi",
            email="locked@example.com", password="Sw0rdfish!23",
        )
        self.model_admin = site._registry[User]
        self.factory = RequestFactory()

    def _lock(self, user):
        """Drive a real lock through the login endpoint, not by setting fields —
        so the test exercises the same state an operator would actually meet."""
        for attempt in range(User.MAX_FAILED_LOGINS):
            self.client.post(
                reverse("token_obtain_pair"),
                data={"email": user.email, "password": "wrong"},
                content_type="application/json",
                REMOTE_ADDR="10.0.0.5",
                HTTP_X_FORWARDED_FOR=f"71.2.3.{attempt}, 10.0.0.5",
            )
        user.refresh_from_db()

    def _run_action(self, queryset):
        request = self.factory.post("/admin/accounts/user/")
        request.user = self.admin
        request.session = "session"
        request._messages = FallbackStorage(request)
        self.model_admin.release_login_lock(request, queryset)

    @override_settings(RATELIMIT_ENABLE=True)
    def test_the_action_lets_a_locked_account_sign_in_again(self):
        self._lock(self.locked)
        self.assertTrue(self.locked.login_locked())
        self.assertTrue(login_guard.email_is_locked(self.locked.email))

        self._run_action(User.objects.filter(pk=self.locked.pk))

        self.locked.refresh_from_db()
        self.assertFalse(self.locked.login_locked())
        self.assertFalse(login_guard.email_is_locked(self.locked.email))

        # The point of the action: the very next attempt actually gets through.
        response = self.client.post(
            reverse("token_obtain_pair"),
            data={"email": self.locked.email, "password": "Sw0rdfish!23"},
            content_type="application/json",
            REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="71.2.3.90, 10.0.0.5",
        )
        self.assertEqual(response.status_code, 200)

    @override_settings(RATELIMIT_ENABLE=True)
    def test_releasing_clears_the_account_rate_buckets_too(self):
        """Clearing only the database counter would look like it worked and then
        refuse the next attempt with a 429 — the failure this covers."""
        self._lock(self.locked)
        before = login_guard.account_limit_state(self.locked.email)
        self.assertTrue(any(t["count"] for t in before["tiers"]))

        self._run_action(User.objects.filter(pk=self.locked.pk))

        after = login_guard.account_limit_state(self.locked.email)
        self.assertEqual([t["count"] for t in after["tiers"]], [0, 0])
        self.assertEqual(after["failures"], 0)

    @override_settings(RATELIMIT_ENABLE=True)
    def test_releasing_one_account_does_not_unblock_another(self):
        other = User.objects.create_user(
            first_name="Bola", last_name="Ade",
            email="other@example.com", password="Sw0rdfish!23",
        )
        self._lock(self.locked)
        self._lock(other)

        self._run_action(User.objects.filter(pk=self.locked.pk))

        other.refresh_from_db()
        self.assertTrue(other.login_locked())
        self.assertTrue(login_guard.email_is_locked(other.email))

    def test_the_filter_finds_locked_accounts_in_sql(self):
        """The filter is a queryset, the model method is per-object; they must
        agree or the changelist lies."""
        self.locked.failed_login_count = User.MAX_FAILED_LOGINS
        self.locked.failed_login_at = timezone.now()
        self.locked.save(update_fields=["failed_login_count", "failed_login_at"])

        request = self.factory.get("/admin/accounts/user/", {"login_lock": "locked"})
        request.user = self.admin
        flt = LoginLockedFilter(request, {"login_lock": ["locked"]}, User, self.model_admin)
        locked_qs = flt.queryset(request, User.objects.all())

        self.assertEqual(list(locked_qs), [self.locked])
        self.assertTrue(all(u.login_locked() for u in locked_qs))

    def test_the_filter_treats_an_aged_out_run_as_unlocked(self):
        """Same ageing rule as login_locked(), or a stale row would show as
        locked in the changelist while the login endpoint let them straight in."""
        self.locked.failed_login_count = User.MAX_FAILED_LOGINS
        self.locked.failed_login_at = (
            timezone.now() - User.FAILED_LOGIN_WINDOW - timedelta(minutes=1)
        )
        self.locked.save(update_fields=["failed_login_count", "failed_login_at"])

        request = self.factory.get("/admin/accounts/user/", {"login_lock": "locked"})
        request.user = self.admin
        flt = LoginLockedFilter(request, {"login_lock": ["locked"]}, User, self.model_admin)

        self.assertEqual(list(flt.queryset(request, User.objects.all())), [])
        self.assertFalse(self.locked.login_locked())

    def test_the_counters_are_readonly_in_the_admin(self):
        """Typing a count by hand would let the row disagree with the cache-side
        counter that shares the lock decision."""
        request = self.factory.get("/admin/accounts/user/")
        request.user = self.admin
        readonly = self.model_admin.get_readonly_fields(request, self.locked)
        self.assertIn("failed_login_count", readonly)
        self.assertIn("failed_login_at", readonly)

    def test_the_status_column_renders_all_three_states(self):
        self.assertEqual(self.model_admin.login_status(self.locked), "—")

        self.locked.failed_login_count = 2
        self.locked.failed_login_at = timezone.now()
        self.assertIn("2/", self.model_admin.login_status(self.locked))

        self.locked.failed_login_count = User.MAX_FAILED_LOGINS
        self.assertIn("Locked", self.model_admin.login_status(self.locked))


# Rendering the admin login page needs a staticfiles backend that isn't the
# hashed/manifest one — there is no collectstatic output under the test runner,
# so ManifestStaticFilesStorage raises on the first {% static %} tag. Nothing to
# do with the guard; it just means any test that renders admin HTML must opt out.
_PLAIN_STATIC = {
    **settings.STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(RATELIMIT_ENABLE=True, STORAGES=_PLAIN_STATIC)
class AdminLoginIsGuardedTests(TestCase):
    """
    /admin/login/ used to have no rate limit and no lockout of any kind — not a
    DRF view, so DEFAULT_THROTTLE_CLASSES never ran, and not wrapped by the
    decorators in accounts/urls.py. Unlimited guesses against any is_staff
    account, and an account locked out of the API could still sign in here.
    """

    ADMIN_LOGIN = "/admin/login/"

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        freeze_rate_limit_window(self)
        self.password = "Sw0rdfish!23"
        self.staff = User.objects.create_superuser(
            first_name="Root", last_name="Admin",
            email="root@example.com", password=self.password,
        )

    def _post(self, password, email="root@example.com", client_ip="81.2.3.4"):
        return self.client.post(
            self.ADMIN_LOGIN,
            data={"username": email, "password": password},
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_FOR=f"{client_ip}, 10.0.0.5",
        )

    # ── routing ─────────────────────────────────────────────────────────

    def test_the_guard_owns_the_admin_login_url(self):
        """Ordered before admin.site.urls in config/urls.py, so the stock view
        never sees the request — and reverse('admin:login') still points here,
        which is what makes every 'log in first' redirect covered."""
        self.assertEqual(resolve(self.ADMIN_LOGIN).func, guarded_admin_login)
        self.assertEqual(reverse("admin:login"), self.ADMIN_LOGIN)

    def test_a_get_renders_the_form_and_is_not_counted(self):
        """Counting page loads would let a refresh spend an operator's
        allowance."""
        for _ in range(20):
            self.assertEqual(self.client.get(self.ADMIN_LOGIN).status_code, 200)

    # ── it still works ──────────────────────────────────────────────────

    def test_a_correct_password_still_signs_in(self):
        response = self._post(self.password)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.staff.pk)

    def test_repeated_correct_logins_are_never_refused(self):
        """Failures only, same as the API: an operator signing in all day must
        not exhaust anything."""
        limit, _ = _split_rate(settings.RATE_LIMITS["admin_login"])
        for attempt in range(limit + 4):
            self.client.logout()
            self.assertEqual(self._post(self.password).status_code, 302, f"login {attempt + 1}")

    # ── the limits ──────────────────────────────────────────────────────

    def test_failed_attempts_hit_the_per_ip_limit(self):
        limit, _ = _split_rate(settings.RATE_LIMITS["admin_login"])
        for attempt in range(limit):
            self._post("wrong", email=f"ghost{attempt}@example.com")
        blocked = self._post("wrong", email="ghost-last@example.com")
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["code"], "rate_limited")

    def test_the_429_carries_a_real_retry_after(self):
        limit, _ = _split_rate(settings.RATE_LIMITS["admin_login"])
        for attempt in range(limit):
            self._post("wrong", email=f"ghost{attempt}@example.com")
        blocked = self._post("wrong", email="ghost-last@example.com")
        self.assertGreaterEqual(int(blocked.headers["Retry-After"]), 1)

    # ── the lock, and the bypass it closes ──────────────────────────────

    def test_the_account_lock_applies_here_too(self):
        for attempt in range(User.MAX_FAILED_LOGINS):
            self._post("wrong", client_ip=f"82.2.3.{attempt}")

        self.staff.refresh_from_db()
        self.assertTrue(self.staff.login_locked())

        # The CORRECT password, and it must still be refused. A redirect back to
        # the form, because that is what throws the submitted credentials away —
        # re-rendering through admin.site.login would hand it this POST and sign
        # the locked account straight in.
        response = self._post(self.password, client_ip="82.2.3.90")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], self.ADMIN_LOGIN)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_the_bounce_carries_the_lockout_message(self):
        """Without this the redirect is silent, and a locked operator sees a
        login form that looks exactly like a mistyped password — so they retype
        it forever instead of resetting, which is the one thing that recovers
        them."""
        for attempt in range(User.MAX_FAILED_LOGINS):
            self._post("wrong", client_ip=f"87.2.3.{attempt}")

        self._post(self.password, client_ip="87.2.3.90")
        form = self.client.get(self.ADMIN_LOGIN)
        self.assertContains(form, "Too many failed sign-in attempts")
        self.assertContains(form, "release_login_lock")

    def test_the_bounce_keeps_where_they_were_headed(self):
        """get_full_path, not a hardcoded '/admin/login/': the admin sends every
        'log in first' redirect here with ?next=, and dropping it would strand
        the operator on the index after they recover."""
        for attempt in range(User.MAX_FAILED_LOGINS):
            self._post("wrong", client_ip=f"88.2.3.{attempt}")

        target = f"{self.ADMIN_LOGIN}?next=/admin/accounts/user/"
        response = self.client.post(
            target,
            data={"username": "root@example.com", "password": self.password},
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_FOR="88.2.3.90, 10.0.0.5",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], target)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_an_account_locked_via_the_api_cannot_walk_in_through_the_admin(self):
        """The bypass. Before this, the API's five-strike lock was decorative
        for exactly the accounts worth attacking."""
        for attempt in range(User.MAX_FAILED_LOGINS):
            self.client.post(
                reverse("token_obtain_pair"),
                data={"email": "root@example.com", "password": "wrong"},
                content_type="application/json",
                REMOTE_ADDR="10.0.0.5",
                HTTP_X_FORWARDED_FOR=f"83.2.3.{attempt}, 10.0.0.5",
            )
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.login_locked())

        response = self._post(self.password, client_ip="83.2.3.90")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], self.ADMIN_LOGIN)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_someone_elses_flood_from_your_ip_does_not_lock_your_account(self):
        """The two axes are independent, and this is the case that proves it.

        A guesser sharing your office IP hammers an address that is not yours.
        The lock is keyed on the EMAIL typed, so it lands on their invented
        address; your account is untouched. Nobody else's traffic can lock you
        out of your own admin — only attempts against YOUR address can.
        """
        for _ in range(User.MAX_FAILED_LOGINS):
            self._post("wrong", email="nobody@example.com", client_ip="91.2.3.4")

        self.staff.refresh_from_db()
        self.assertEqual(self.staff.failed_login_count, 0)
        self.assertFalse(self.staff.login_locked())

    def test_but_their_flood_does_spend_the_shared_ip_budget(self):
        """The other half of the same story, and the honest cost of an IP tier.

        The per-IP limit cannot tell two people behind one address apart, so a
        guesser on your connection CAN spend the admin door's budget and leave
        you refused — as a 429, not a lockout, and only until the window rolls.
        Switching networks is the immediate way out, because the limit describes
        a machine rather than an account.
        """
        limit, _ = _split_rate(settings.RATE_LIMITS["admin_login"])
        for _ in range(limit):
            self._post("wrong", email="nobody@example.com", client_ip="91.2.3.4")

        blocked = self._post(self.password, client_ip="91.2.3.4")
        self.assertEqual(blocked.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

        # Same account, same correct password, a different connection.
        elsewhere = self._post(self.password, client_ip="92.9.9.9")
        self.assertEqual(elsewhere.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.staff.pk)

    def test_superusers_are_not_exempt(self):
        """They are the highest-value target; exempting them would defeat the
        control. Safe because of the two recovery paths, both tested below."""
        self.assertTrue(self.staff.is_superuser)
        for attempt in range(User.MAX_FAILED_LOGINS):
            self._post("wrong", client_ip=f"84.2.3.{attempt}")
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.login_locked())

    def test_a_locked_and_an_unknown_address_look_the_same(self):
        """Otherwise the admin login page answers 'is there an account here?'
        for the price of five wrong passwords."""
        for attempt in range(User.MAX_FAILED_LOGINS):
            self._post("wrong", client_ip=f"85.2.3.{attempt}")
        real = self._post("wrong", client_ip="85.2.3.90")

        cache.clear()
        for attempt in range(User.MAX_FAILED_LOGINS):
            self._post("wrong", email="ghost@example.com", client_ip=f"86.2.3.{attempt}")
        ghost = self._post("wrong", email="ghost@example.com", client_ip="86.2.3.90")

        self.assertEqual(real.status_code, ghost.status_code)


class ReleaseLoginLockCommandTests(TestCase):
    """The break-glass. Without it, a lock on /admin/login/ would be
    self-referential — the release button behind the locked door."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_superuser(
            first_name="Root", last_name="Admin",
            email="root@example.com", password="Sw0rdfish!23",
        )

    def _lock(self, user):
        user.failed_login_count = User.MAX_FAILED_LOGINS
        user.failed_login_at = timezone.now()
        user.save(update_fields=["failed_login_count", "failed_login_at"])
        for _ in range(User.MAX_FAILED_LOGINS):
            login_guard.record_email_failure(user.email)

    def test_it_clears_both_stores_not_just_the_row(self):
        """A hand-rolled ORM update would clear the columns, look like it
        worked, and hand you a 429 on the next attempt."""
        self._lock(self.user)
        self.assertTrue(self.user.login_locked())
        self.assertTrue(login_guard.email_is_locked(self.user.email))

        call_command("release_login_lock", "root@example.com", stdout=StringIO())

        self.user.refresh_from_db()
        self.assertFalse(self.user.login_locked())
        self.assertFalse(login_guard.email_is_locked(self.user.email))

    def test_it_is_case_insensitive_about_the_address(self):
        self._lock(self.user)
        call_command("release_login_lock", "ROOT@Example.com", stdout=StringIO())
        self.user.refresh_from_db()
        self.assertFalse(self.user.login_locked())

    def test_all_releases_every_locked_account(self):
        """The both-admins-locked case, which is the whole reason this exists."""
        other = User.objects.create_superuser(
            first_name="Two", last_name="Admin",
            email="two@example.com", password="Sw0rdfish!23",
        )
        self._lock(self.user)
        self._lock(other)

        call_command("release_login_lock", "--all", stdout=StringIO())

        self.user.refresh_from_db()
        other.refresh_from_db()
        self.assertFalse(self.user.login_locked())
        self.assertFalse(other.login_locked())

    def test_an_unknown_address_is_an_error_not_a_silent_no_op(self):
        with self.assertRaises(CommandError):
            call_command("release_login_lock", "nobody@example.com", stdout=StringIO())

    def test_it_requires_an_address_or_all(self):
        with self.assertRaises(CommandError):
            call_command("release_login_lock", stdout=StringIO())


@override_settings(RATELIMIT_ENABLE=True)
class RateLimitWindowFreezeTests(TestCase):
    """Pins `freeze_rate_limit_window`. Remove the freeze and five tests in this
    file go intermittently red on CI with "401 != 429".

    Two halves, and both are needed: the first proves the hazard is real, so the
    freeze is not cargo cult; the second proves the helper actually removes it.
    """

    # What bucket_ip() yields for the client the login tests post as.
    BUCKET = "41.2.3.4"
    PERIOD = 60  # seconds, from the "10/m" on auth_login

    def test_the_window_moves_with_the_wall_clock(self):
        """The hazard. django-ratelimit's window is part of the cache key, so
        when it changes mid-burst the counter silently starts again from zero."""
        seen = set()
        for offset in (0, self.PERIOD, self.PERIOD * 2):
            with patch("django_ratelimit.core.time.time", return_value=_FROZEN_CLOCK + offset):
                seen.add(_get_window(self.BUCKET, self.PERIOD))
        self.assertGreater(
            len(seen), 1,
            "django-ratelimit's window no longer tracks the clock — if it became "
            "a sliding window, freeze_rate_limit_window and this test can go.",
        )

    def test_the_freeze_hides_the_wall_clock_from_django_ratelimit(self):
        """The fix. Once applied, the library cannot observe real time at all,
        so no burst can straddle an edge however slow the runner is."""
        freeze_rate_limit_window(self)
        self.assertEqual(ratelimit_core.time.time(), _FROZEN_CLOCK)

    def test_every_class_driving_a_bucket_to_its_limit_freezes_the_window(self):
        """The one that catches a NEW test class written without the freeze.

        Asserting a 429 means asserting an exact ceiling, and that is only
        deterministic inside one window.
        """
        import inspect

        unfrozen = []
        for name, obj in sorted(globals().items()):
            if not (inspect.isclass(obj) and issubclass(obj, TestCase)):
                continue
            if obj is RateLimitWindowFreezeTests:
                continue
            body = inspect.getsource(obj)
            asserts_429 = "status_code, 429" in body or ", 429)" in body
            if asserts_429 and "freeze_rate_limit_window" not in body:
                unfrozen.append(name)

        self.assertEqual(
            unfrozen, [],
            "These classes assert a 429 without freezing the rate-limit window, "
            "so they will flake on a slow runner. Call "
            "freeze_rate_limit_window(self) in setUp: " + ", ".join(unfrozen),
        )
