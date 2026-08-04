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

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(RATELIMIT_ENABLE=True)
class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()  # reset django-ratelimit (and DRF throttle) counters
        # Freeze the clock so django-ratelimit's fixed-window counter can't roll
        # over mid-test. Without this, a request burst that straddles a window
        # boundary resets the counter and the call that should be blocked slips
        # through as 401 — the flaky "401 != 429". (core._get_window uses time.time)
        patcher = patch("django_ratelimit.core.time.time", return_value=1_700_000_000.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        cache.clear()

    def _post(self, url, **body):
        return self.client.post(url, data=body, content_type="application/json")

    def test_login_blocks_after_limit_with_standard_429_envelope(self):
        url = reverse("token_obtain_pair")
        # Default RATE_LIMIT_AUTH_LOGIN=5/m — first 5 pass the limiter (they 401
        # on bad creds), the 6th is blocked before the view runs.
        for _ in range(5):
            resp = self._post(url, email="x@example.com", password="wrong")
            self.assertNotEqual(resp.status_code, 429)

        blocked = self._post(url, email="x@example.com", password="wrong")
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

    @override_settings(RATELIMIT_ENABLE=False)
    def test_no_throttling_when_disabled(self):
        url = reverse("token_obtain_pair")
        for _ in range(8):  # well past the 5/m limit
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
        from apps.portal.models import ClientPortal, EventEngagement

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
