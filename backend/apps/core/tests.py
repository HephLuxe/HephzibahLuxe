import datetime
import io
import re
import threading
import time
import uuid
from unittest.mock import patch

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.files.base import File
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.test import TestCase, override_settings
from django.urls import reverse
from django_ratelimit.core import _get_window, _make_cache_key, _split_rate
from PIL import Image
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIRequestFactory

from apps.core import background, uploads
from apps.core.ratelimit import bucket_ip, client_ip, resolve_client_ip
from apps.core.throttling import ClientIPAnonRateThrottle, UserBurstRateThrottle
from apps.core.uploads import validate_document, validate_image, validate_photo
from apps.core.utils import save_with_attribution, stamp_attribution, user_display_name
from apps.document_hub.serializers import ClientDocumentSerializer
from apps.events.models import Event
from apps.events.serializers import EventDaySerializer, EventSerializer


class HealthEndpointTests(TestCase):
    """The observability standard's probes. /health/ must be a bare,
    unauthenticated 200 (the home-server control panel depends on it)."""

    def test_health_live_is_unauthenticated_200(self):
        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_health_ready_ok_when_db_reachable(self):
        resp = self.client.get("/health/ready/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    # ── the failure branches ────────────────────────────────────────────
    # Untested until now, which is exactly how this endpoint spent its life
    # returning str(exc) — the leak only rendered during an outage, and no test
    # ever produced one. These drive both branches with a REALISTIC driver
    # message so the assertions are about the real string, not a stand-in.

    _PSYCOPG_MESSAGE = (
        'connection to server at "ep-tiny-rice-123.us-east-2.aws.neon.tech" '
        '(3.143.47.40), port 5432 failed: FATAL:  password authentication '
        'failed for user "neondb_owner"'
    )
    _REDIS_MESSAGE = "Error 111 connecting to redis.railway.internal:6379. Connection refused."

    def test_a_dead_db_reports_503_without_naming_the_host_or_role(self):
        """The probe must say WHICH dependency died and nothing else.

        /health/ready/ is unauthenticated and unthrottled by design, so anything
        in this body is public. The driver's own message carries the Neon
        hostname, its resolved IP, the port and the database role — half a
        credential pair, published at exactly the moment things are going wrong.
        """
        with patch(
            "django.db.connection.ensure_connection",
            side_effect=Exception(self._PSYCOPG_MESSAGE),
        ):
            resp = self.client.get("/health/ready/")

        self.assertEqual(resp.status_code, 503)
        body = resp.content.decode()
        self.assertEqual(resp.json(), {"status": "error", "errors": {"db": "unreachable"}})
        for secret in ("neon.tech", "3.143.47.40", "5432", "neondb_owner", "password"):
            self.assertNotIn(secret, body, f"{secret!r} leaked into the public body")

    def test_a_dead_cache_reports_503_without_naming_the_host(self):
        """Leaks less than the db branch — redis-py renders no password — but
        `redis.railway.internal:6379` is still internal topology."""
        with patch(
            "django.core.cache.cache.get",
            side_effect=Exception(self._REDIS_MESSAGE),
        ):
            resp = self.client.get("/health/ready/")

        self.assertEqual(resp.status_code, 503)
        body = resp.content.decode()
        self.assertEqual(resp.json(), {"status": "error", "errors": {"cache": "unreachable"}})
        for secret in ("railway.internal", "6379"):
            self.assertNotIn(secret, body, f"{secret!r} leaked into the public body")

    def test_the_detail_still_reaches_the_log(self):
        """Suppressed in the response, NOT discarded — an operator still needs
        the driver message, it just belongs somewhere authenticated."""
        with patch(
            "django.db.connection.ensure_connection",
            side_effect=Exception(self._PSYCOPG_MESSAGE),
        ):
            with self.assertLogs("apps.core.views", level="ERROR") as captured:
                self.client.get("/health/ready/")

        self.assertIn(self._PSYCOPG_MESSAGE, "\n".join(captured.output))

    def test_the_runbook_can_still_tell_the_two_dependencies_apart(self):
        """RUNBOOK reads `errors.db` vs `errors.cache` to separate a bad
        DATABASE_URL from a bad CACHE_REDIS_URL. Dropping the detail must not
        cost that — it is the whole diagnostic value of the endpoint."""
        with patch(
            "django.db.connection.ensure_connection", side_effect=Exception("boom"),
        ), patch(
            "django.core.cache.cache.get", side_effect=Exception("boom"),
        ):
            resp = self.client.get("/health/ready/")

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(sorted(resp.json()["errors"]), ["cache", "db"])


class RequestIdMiddlewareTests(TestCase):
    def test_response_carries_request_id_header(self):
        resp = self.client.get("/health/")
        self.assertTrue(resp.headers.get("X-Request-ID"))

    def test_inbound_request_id_is_echoed_back(self):
        resp = self.client.get("/health/", headers={"x-request-id": "abc123"})
        self.assertEqual(resp.headers.get("X-Request-ID"), "abc123")


class AttributionTests(TestCase):
    """The shared created_by/last_updated_by attribution layer (AttributedModel,
    the stamping helpers, and AttributionSerializerMixin), exercised through
    Event as a representative attributed model."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create_user(
            first_name="Winnie", last_name="Adeyemi", email="attr-staff@example.com",
            password="x", role="staff",
        )
        cls.other = User.objects.create_user(
            first_name="Tosin", last_name="Bello", email="attr-other@example.com",
            password="x", role="staff",
        )
        cls.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="attr-client@example.com", password="x",
        )

    def _event(self, **extra):
        return Event.objects.create(
            celebrant=self.client_user, title="P & S", event_type="Wedding",
            bride_name="Priscilla", groom_name="Samuel", country="NG", state="Lagos",
            event_date=datetime.date(2027, 1, 1), **extra,
        )

    def test_create_stamps_created_by_only_not_last_updated_by(self):
        """A new row has a creator and no editor.

        This used to stamp both, which made "never edited" and "edited by its
        own creator" indistinguishable — a freshly created event came back
        already naming a last editor, so the API reported an edit that had not
        happened. NULL last_updated_by is now load-bearing: it is the only thing
        that says nobody has touched the row since it was made.
        """
        serializer = EventSerializer(data={
            "title": "x", "country": "NG", "state": "Lagos",
            "event_date": "2027-01-01", "event_type": "Wedding",
            "bride_name": "P", "groom_name": "S",
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        event = save_with_attribution(serializer, self.staff, celebrant=self.client_user)
        self.assertEqual(event.created_by, self.staff)
        self.assertIsNone(event.last_updated_by)
        # …and that reads through to the API surface as an empty name.
        self.assertEqual(EventSerializer(event).data["last_updated_by_display"], "")

    def test_registering_a_client_attributes_both_the_user_and_their_portal(self):
        """The portal a signal creates inherits the actor from the user row.

        ClientPortal is created by a post_save receiver, which has no request
        and therefore no request.user — so it could never be stamped directly,
        and every auto-created portal came back with created_by_display: "".
        The actor now rides in on User.created_by, set before the save that
        fires the signal.
        """
        User = get_user_model()
        onboarded = User.objects.create_user(
            first_name="Nkem", last_name="Vante", email="attr-onboarded@example.com",
            password="x", role="client", created_by=self.staff,
        )
        self.assertEqual(onboarded.created_by, self.staff)
        self.assertEqual(onboarded.portal.created_by, self.staff)
        # Creation is not an edit — neither row claims a last editor.
        self.assertIsNone(onboarded.last_updated_by)
        self.assertIsNone(onboarded.portal.last_updated_by)

    def test_self_registration_leaves_attribution_null(self):
        """No actor, no attribution — create_user without a registrar."""
        User = get_user_model()
        alone = User.objects.create_user(
            first_name="Solo", last_name="Client", email="attr-solo@example.com",
            password="x", role="client",
        )
        self.assertIsNone(alone.created_by)
        self.assertIsNone(alone.portal.created_by)

    def test_update_changes_last_updated_by_but_not_created_by(self):
        event = self._event(created_by=self.staff, last_updated_by=self.staff)
        serializer = EventSerializer(event, data={"description": "edited"}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        save_with_attribution(serializer, self.other)
        event.refresh_from_db()
        self.assertEqual(event.created_by, self.staff)        # unchanged
        self.assertEqual(event.last_updated_by, self.other)   # now the editor

    def test_serializer_exposes_names_and_never_raw_ids(self):
        event = self._event(created_by=self.staff, last_updated_by=self.other)
        data = EventSerializer(event).data
        self.assertEqual(data["created_by_display"], "Winnie Adeyemi")
        self.assertEqual(data["last_updated_by_display"], "Tosin Bello")
        # The raw FK ids must never leak (the `last_updated_by: 1` bug).
        self.assertNotIn("created_by", data)
        self.assertNotIn("last_updated_by", data)

    def test_display_is_blank_when_unset(self):
        data = EventSerializer(self._event()).data
        self.assertEqual(data["created_by_display"], "")
        self.assertEqual(data["last_updated_by_display"], "")

    def test_stamp_attribution_ignores_anonymous(self):
        from django.contrib.auth.models import AnonymousUser
        event = self._event()
        stamp_attribution(event, AnonymousUser())
        self.assertIsNone(event.last_updated_by_id)

    def test_display_falls_back_to_email_without_a_name(self):
        User = get_user_model()
        nameless = User(first_name="", last_name="", email="ops@example.com")
        self.assertEqual(user_display_name(nameless), "ops@example.com")


# ═════════════════════════════════════════════════════════════════════════════
#  Rate limiting — the shared machinery
#
#  These pin the invariants in docs/RATE_LIMITING_AUDIT.md §5. Each one exists
#  because its absence let a real defect ship, so the assertion is on observed
#  behaviour (a bucket identity, a cache key) rather than on a call succeeding.
# ═════════════════════════════════════════════════════════════════════════════

class ClientIPResolutionTests(TestCase):
    """
    One answer to "who is the client", and it must not be forgeable.

    The rightmost-untrusted walk is the whole reason X-Forwarded-For is safe to
    read at all: every proxy in both deployment shapes APPENDS, so anything the
    caller prepends sits to the left of the real address and is never reached.
    """

    def _request(self, xff=None, remote="10.0.0.5"):
        extra = {"REMOTE_ADDR": remote}
        if xff is not None:
            extra["HTTP_X_FORWARDED_FOR"] = xff
        return APIRequestFactory().post("/x", {}, format="json", **extra)

    def test_prepended_xff_is_ignored(self):
        """A caller cannot escape their bucket by prepending addresses.

        This is the defect DRF's own get_ident has: it uses the whole header as
        the identity, so one extra entry is one fresh bucket, unlimited times.
        """
        honest = bucket_ip(self._request("41.2.3.4, 10.0.0.5"))
        for spoof in ("9.9.9.9, 41.2.3.4, 10.0.0.5",
                      "8.8.8.8, 7.7.7.7, 41.2.3.4, 10.0.0.5"):
            self.assertEqual(bucket_ip(self._request(spoof)), honest, spoof)
        self.assertEqual(honest, "41.2.3.4")

    def test_non_ip_xff_entries_never_become_a_bucket(self):
        """Garbage is not an identity.

        Unvalidated text used to be returned verbatim, which is how a long
        header reached a varchar(45) audit column and 500'd the endpoint.
        """
        self.assertEqual(bucket_ip(self._request("JUNK, 41.2.3.4, 10.0.0.5")), "41.2.3.4")
        # Nothing usable at all: fall back to the proxy address rather than
        # returning text that is not an address.
        self.assertEqual(resolve_client_ip(self._request("NOT-AN-IP")), "10.0.0.5")

    def test_a_direct_connection_ignores_xff_entirely(self):
        """Proxy bypassed: REMOTE_ADDR is public, so XFF is fully attacker-owned."""
        self.assertEqual(bucket_ip(self._request("9.9.9.9", remote="41.2.3.4")), "41.2.3.4")

    def test_ipv6_is_bucketed_by_prefix_not_by_address(self):
        """Two addresses in one /64 share a bucket.

        A residential IPv6 allocation is a /64 or larger and the client owns
        every address in it, so an unmasked key hands them a new bucket per
        request and every IP-keyed limit in the project becomes free to bypass.
        """
        a = bucket_ip(self._request("2001:db8:abcd:1234::1, 10.0.0.5"))
        b = bucket_ip(self._request("2001:db8:abcd:1234::beef, 10.0.0.5"))
        self.assertEqual(a, b)
        self.assertEqual(a, "2001:db8:abcd:1234::")
        # A different /64 is still a different bucket — the mask must not be so
        # wide that unrelated clients merge.
        self.assertNotEqual(bucket_ip(self._request("2001:db8:abcd:9999::1, 10.0.0.5")), a)

    def test_resolve_keeps_the_exact_address_for_the_audit_trail(self):
        """bucket_ip masks; resolve_client_ip must not.

        The audit column wants precision, the limiter wants the prefix. One
        resolver, two derived forms — if these ever return the same thing for
        IPv6, the audit trail has silently lost information.
        """
        request = self._request("2001:db8:abcd:1234::beef, 10.0.0.5")
        self.assertEqual(resolve_client_ip(request), "2001:db8:abcd:1234::beef")
        self.assertNotEqual(resolve_client_ip(request), bucket_ip(request))

    def test_every_consumer_agrees_on_one_identity(self):
        """The single most important assertion here.

        django-ratelimit's key callable, DRF's throttle ident and the value
        written to the audit trail must be the same client for the same request.
        Three implementations used to exist and two were wrong, so a 429 could
        not be reproduced or attributed.
        """
        request = self._request("9.9.9.9, 41.2.3.4, 10.0.0.5")
        request.user = AnonymousUser()

        from_key_callable = client_ip(None, request)
        from_drf_throttle = ClientIPAnonRateThrottle().get_ident(request)
        from_audit_trail = resolve_client_ip(request)

        self.assertEqual(from_key_callable, "41.2.3.4")
        self.assertEqual(from_drf_throttle, "41.2.3.4")
        self.assertEqual(from_audit_trail, "41.2.3.4")

    def test_a_resolved_address_always_fits_the_audit_column(self):
        """PasswordResetToken.ip_address is varchar(45).

        A 46-character value raises DataError on INSERT, which escaped as a 500
        and (because only DoesNotExist was caught) turned the endpoint's
        anti-enumeration guarantee into an oracle. Validation makes the overflow
        structurally impossible, so pin the property rather than the symptom.
        """
        longest_v6 = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"
        cases = ["41.2.3.4", longest_v6, f"{longest_v6}, 10.0.0.5"]
        for xff in cases:
            self.assertLessEqual(len(resolve_client_ip(self._request(xff))), 45, xff)
        # And a header far longer than the column cannot produce a long value.
        overlong = ", ".join(["9" * 60] * 5) + ", 41.2.3.4, 10.0.0.5"
        self.assertEqual(resolve_client_ip(self._request(overlong)), "41.2.3.4")


class RateLimitGroupIsolationTests(TestCase):
    """
    No two rate-limited endpoints may share a counter.

    django-ratelimit derives its `group` from the view's __module__ +
    __qualname__ when none is given, and EVERY as_view() result carries the
    qualname "View.as_view.<locals>.view" — so two endpoints in one module with
    the same rate and key callable land on one cache key. password_reset_verify
    and password_reset_confirm (both 10/m on client_ip in apps.accounts.views)
    did exactly that, which meant using up verify attempts blocked confirming.

    Every limit now declares `group=`, and these tests are what stop the next
    endpoint from reintroducing the collision.
    """

    # The key value each group counts against, for one fixed client. Shapes
    # differ (IP / IP+email / email) because the key callables differ.
    KEY_VALUES = {
        "auth_login": "41.2.3.4",
        "auth_login_account": "a@b.com",
        "auth_login_account_daily": "a@b.com",
        "auth_login_daily": "41.2.3.4",
        "admin_login": "41.2.3.4",
        "admin_login_daily": "41.2.3.4",
        "token_refresh": "41.2.3.4",
        "token_refresh_daily": "41.2.3.4",
        "password_reset_request": "41.2.3.4:a@b.com",
        "password_reset_request_daily": "41.2.3.4",
        "password_reset_verify": "41.2.3.4",
        "password_reset_verify_daily": "41.2.3.4",
        "password_reset_confirm": "41.2.3.4",
        "password_reset_confirm_daily": "41.2.3.4",
        "inquiry_submit_burst": "41.2.3.4:a@b.com",
        "inquiry_submit_ip": "41.2.3.4",
    }

    def _cache_key(self, group, rate, value):
        _, period = _split_rate(rate)
        return _make_cache_key(group, _get_window(value, period), rate, value, "POST")

    def test_every_configured_limit_has_a_key_value_here(self):
        """Guard on the guard: a new RATE_LIMITS entry must be added below too,
        or the isolation test would silently stop covering it."""
        self.assertEqual(set(settings.RATE_LIMITS), set(self.KEY_VALUES))

    def test_no_two_limits_share_a_cache_key(self):
        keys = {
            group: self._cache_key(group, rate, self.KEY_VALUES[group])
            for group, rate in settings.RATE_LIMITS.items()
        }
        collisions = {}
        for group, key in keys.items():
            collisions.setdefault(key, []).append(group)
        shared = [groups for groups in collisions.values() if len(groups) > 1]
        self.assertEqual(shared, [], f"limits sharing a bucket: {shared}")

    def test_password_reset_verify_and_confirm_are_separate_buckets(self):
        """The specific pair that shipped broken. Same rate, same key callable,
        same module — separated only by their declared group."""
        rate = settings.RATE_LIMITS["password_reset_verify"]
        self.assertEqual(rate, settings.RATE_LIMITS["password_reset_confirm"])
        self.assertNotEqual(
            self._cache_key("password_reset_verify", rate, "41.2.3.4"),
            self._cache_key("password_reset_confirm", rate, "41.2.3.4"),
        )

    def test_each_anonymous_endpoint_has_its_own_day(self):
        """The fix for the shared daily ceiling.

        Five of the six are `<n>/d` keyed on the client IP, so before groups were
        declared they were the textbook collision — and while the DAILY cap came
        from the shared DRF `anon` throttle they genuinely were one bucket, so a
        morning of failed logins could refuse someone else's password reset.

        The sixth, `auth_login_account_daily`, is keyed on the submitted EMAIL
        rather than the IP (ADR-0002). It is included here anyway: the property
        under test is that no two daily caps share a bucket, and driving them all
        with one value is the strictest way to check that — if the groups did not
        separate them, identical key values would collide.
        """
        daily = {g: r for g, r in settings.RATE_LIMITS.items() if g.endswith("_daily")}
        self.assertEqual(len(daily), 7, f"expected seven daily caps, got {sorted(daily)}")

        keys = {
            group: self._cache_key(group, rate, "41.2.3.4")
            for group, rate in daily.items()
        }
        self.assertEqual(len(set(keys.values())), len(keys), keys)

    def test_the_shared_anon_ceiling_is_looser_than_the_daily_caps_it_replaced(self):
        """`anon` is now a SAFETY NET for endpoints nobody wired a limit onto, so
        it must never be what binds on a legitimate caller. If it drops below the
        per-endpoint caps it silently becomes the primary limit again, and the
        cross-endpoint starvation this fix removed comes straight back."""
        anon_count, _ = UserBurstRateThrottle.parse_rate(
            UserBurstRateThrottle, settings.THROTTLE_RATES["anon"]
        )
        biggest_daily = max(
            _split_rate(rate)[0]
            for group, rate in settings.RATE_LIMITS.items()
            if group.endswith("_daily")
        )
        self.assertGreater(anon_count, biggest_daily)

    def test_inquiry_and_password_reset_do_not_share_a_bucket(self):
        """Cross-app isolation must not depend on which file a view lives in.

        inquiry_submit_burst and password_reset_request are both keyed on
        (IP, email) over POST; before groups were declared, only fn.__module__
        kept the public lead-capture endpoint out of the portal's bucket.
        """
        self.assertNotEqual(
            self._cache_key("inquiry_submit_burst",
                            settings.RATE_LIMITS["inquiry_submit_burst"], "41.2.3.4:a@b.com"),
            self._cache_key("password_reset_request",
                            settings.RATE_LIMITS["password_reset_request"], "41.2.3.4:a@b.com"),
        )


class ThrottleScopeTests(TestCase):
    """
    The two project-wide ceilings, and who they apply to.

    The dividing line is ACCOUNTABLE vs ANONYMOUS — not client vs staff.
    """

    def _request(self, xff="41.2.3.4, 10.0.0.5"):
        return APIRequestFactory().post(
            "/x", {}, format="json",
            REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR=xff,
        )

    def test_anon_ceiling_is_not_escapable_by_spoofing(self):
        """DRF's stock get_ident uses the whole XFF header, so one prepended
        address is one fresh bucket. The subclass must not."""
        throttle = ClientIPAnonRateThrottle()
        request = self._request()
        request.user = AnonymousUser()
        spoofed = self._request("9.9.9.9, 41.2.3.4, 10.0.0.5")
        spoofed.user = AnonymousUser()
        self.assertEqual(throttle.get_cache_key(request, None),
                         throttle.get_cache_key(spoofed, None))

    def test_anon_ceiling_never_applies_to_an_account_holder(self):
        """AnonRateThrottle returns None once authenticated. Pinned because the
        architecture puts the anon ceiling exclusively on the anonymous surface;
        if this ever changed, account-holders would silently gain a second
        limit."""
        request = self._request()
        request.user = get_user_model()(email="x@example.com")
        request.user.set_unusable_password()
        self.assertTrue(request.user.is_authenticated)
        self.assertIsNone(ClientIPAnonRateThrottle().get_cache_key(request, None))

    def test_the_old_daily_user_budget_is_gone(self):
        """`user: 500/day` was a sliding daily budget: a runaway frontend loop
        burned it in seconds and then locked a real person out for hours. It was
        deleted, not raised — if the scope reappears, that regression is back."""
        self.assertNotIn("user", settings.THROTTLE_RATES)
        self.assertIn("user_burst", settings.THROTTLE_RATES)

    def test_the_authenticated_ceiling_is_a_burst_not_a_budget(self):
        """The window is the point. A per-minute ceiling stops a loop almost
        immediately and clears itself; anything measured in hours or days cannot.

        Asserted against settings.THROTTLE_RATES — the declared policy — because
        the test runner nulls the *active* rates to switch throttling off, and
        the choice being pinned here is the configuration, not the runtime.
        """
        rate = settings.THROTTLE_RATES["user_burst"]
        count, duration = UserBurstRateThrottle.parse_rate(UserBurstRateThrottle, rate)
        self.assertLessEqual(duration, 60, f"{rate} is not a burst window")
        self.assertGreaterEqual(count, 60, f"{rate} is tight enough to catch a human")

    def test_throttling_is_disabled_under_the_test_runner(self):
        """Both limiters off in tests, and off the same way: by neutralising the
        rate, not by unwiring the classes — so every view still reports the
        ceilings it really carries."""
        self.assertFalse(settings.RATELIMIT_ENABLE)
        active = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertTrue(all(rate is None for rate in active.values()), active)
        self.assertTrue(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"])

    def test_staff_and_clients_are_limited_identically(self):
        """No role split on the accountable surface.

        Staff have the highest blast radius AND do the most legitimate
        high-volume work, so a staff-specific limit is the most likely to cause
        an outage and the least likely to stop an attacker. One class, one rate,
        keyed on the account.
        """
        User = get_user_model()
        client_user = User(email="client@example.com", is_staff=False)
        staff_user = User(email="staff@example.com", is_staff=True)
        client_user.id, staff_user.id = uuid.uuid4(), uuid.uuid4()

        throttle = UserBurstRateThrottle()
        keys = []
        for user in (client_user, staff_user):
            request = self._request()
            request.user = user
            keys.append(throttle.get_cache_key(request, None))

        # Different buckets (each account gets its own allowance)…
        self.assertNotEqual(keys[0], keys[1])
        # …but the same scope, so the same rate applies to both.
        self.assertTrue(all(k.startswith("throttle_user_burst_") for k in keys))


class RateLimitConfigurationTests(TestCase):
    """
    Every rate-limiting number is declared in `config/settings.py`, with env as
    an override-only escape hatch. These pin the properties that make that safe.
    """

    def test_every_limit_parses(self):
        """A malformed rate is not a validation error anywhere — django-ratelimit
        regex-matches it and crashes on the None, at request time, on whichever
        endpoint happens to be hit first. An env override typo would do exactly
        that, so parse them all up front."""
        for group, rate in settings.RATE_LIMITS.items():
            count, period = _split_rate(rate)
            self.assertGreater(count, 0, f"{group}={rate}")
            self.assertGreater(period, 0, f"{group}={rate}")

    def test_every_throttle_rate_parses(self):
        for scope, rate in settings.THROTTLE_RATES.items():
            count, duration = UserBurstRateThrottle.parse_rate(
                UserBurstRateThrottle, rate
            )
            self.assertGreater(count, 0, f"{scope}={rate}")
            self.assertGreater(duration, 0, f"{scope}={rate}")

    def test_the_retired_inquiry_limit_is_gone(self):
        """RATE_LIMIT_INQUIRY_SUBMIT was replaced by two tiers. If the old key
        came back, one of the tiers would be unwired and nothing would say so."""
        self.assertNotIn("inquiry_submit", settings.RATE_LIMITS)
        self.assertIn("inquiry_submit_burst", settings.RATE_LIMITS)
        self.assertIn("inquiry_submit_ip", settings.RATE_LIMITS)

    def test_the_near_limit_threshold_is_a_usable_fraction(self):
        fraction = settings.THROTTLE_NEAR_LIMIT_FRACTION
        self.assertGreater(fraction, 0.0)
        self.assertLessEqual(fraction, 1.0)

    def test_the_access_token_window_is_short(self):
        """An access token cannot be revoked — only refresh tokens are
        blacklisted on rotation — so its lifetime is exactly how long a leaked
        one keeps working, with deactivating the account changing nothing. This
        pins that it stays in minutes-to-an-hour territory, not days."""
        lifetime = settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"]
        self.assertLessEqual(lifetime, datetime.timedelta(hours=2))
        # And still long enough that a session isn't refreshing constantly.
        self.assertGreaterEqual(lifetime, datetime.timedelta(minutes=15))


class UploadCeilingTests(TestCase):
    """
    apps/core/uploads.py — the size/type ceiling on writable file fields.

    Before this existed, nine of the eleven file fields accepted a file of any
    size, and three of those nine are writable by a CLIENT. ImageField was doing
    the only filtering, and ImageField has no opinion about size — a 500MB JPEG
    is a perfectly valid image.
    """

    # Real leading bytes per format. The validator sniffs content now, so a
    # stand-in of b"x" would be refused as "no recognised signature" and every
    # size assertion below would pass for the wrong reason.
    MAGIC = {
        "application/pdf": b"%PDF-1.4\n",
        "image/jpeg": b"\xff\xd8\xff\xe0\x00\x10JFIF",
        "image/png": b"\x89PNG\r\n\x1a\n",
        "image/webp": b"RIFF\x24\x00\x00\x00WEBP",
        "application/x-msdownload": b"MZ\x90\x00",
    }

    def _upload(self, *, size, content_type="image/jpeg", name="photo.jpg"):
        """A file that claims a size without allocating it.

        SimpleUploadedFile derives .size from its content, so a genuine 26MB
        fixture would mean holding 26MB per assertion for no benefit — the
        validator reads the size attribute, it does not measure the bytes. The
        CONTENT still has to be right, though: the type half reads the first
        twelve bytes.
        """
        f = SimpleUploadedFile(name, self.MAGIC[content_type], content_type=content_type)
        f.size = size
        return f

    # ── the size ceiling ────────────────────────────────────────────────

    def test_a_file_over_the_ceiling_is_refused(self):
        with self.assertRaises(DRFValidationError) as caught:
            validate_image(self._upload(size=uploads.MAX_IMAGE_SIZE + 1))
        self.assertIn("10MB", str(caught.exception))

    def test_a_file_exactly_on_the_ceiling_is_allowed(self):
        """`>` not `>=`. A limit advertised as 10MB that refuses a 10MB file is
        a support ticket, and the off-by-one is invisible without this."""
        exact = self._upload(size=uploads.MAX_IMAGE_SIZE)
        self.assertIs(validate_image(exact), exact)

    def test_the_message_names_both_numbers(self):
        """"Too large" gives the user nothing to act on — they cannot tell
        whether to re-export or give up."""
        with self.assertRaises(DRFValidationError) as caught:
            validate_document(self._upload(size=41 * uploads.MB, content_type="application/pdf"))
        message = str(caught.exception)
        self.assertIn("41MB", message)   # what they sent
        self.assertIn("25MB", message)   # what they may send

    # ── the type ceiling ────────────────────────────────────────────────

    def test_a_pdf_is_refused_where_only_images_belong(self):
        """Covers the FileField fields, which have no Pillow check to fall back
        on — for those this is the only type control that exists."""
        with self.assertRaises(DRFValidationError):
            validate_image(self._upload(size=1024, content_type="application/pdf"))

    def test_a_pdf_is_accepted_as_a_document(self):
        pdf = self._upload(size=1024, content_type="application/pdf", name="contract.pdf")
        self.assertIs(validate_document(pdf), pdf)

    def test_a_file_already_in_storage_is_let_through(self):
        """Not an upload, so not sniffed.

        A partial update that leaves a file field untouched hands the validator
        the stored File rather than an UploadedFile. Reading it would mean a
        round trip to R2 on every such PATCH, to re-check bytes that were
        validated on the way in — so it is skipped on type, by class rather than
        by a missing attribute.
        """
        stored = File(io.BytesIO(b"not a signature"), name="photo.jpg")
        stored.size = 1024
        self.assertIs(validate_photo(stored), stored)

    # ── the type check reads bytes, not the declared header ─────────────

    def test_a_real_pdf_is_accepted_however_the_caller_labelled_it(self):
        """The false-rejection this replaced.

        `application/octet-stream` is the documented fallback for any sender
        without an extension->MIME table — curl -F, several mobile pickers,
        Postman with a stale file reference. Those uploads were refused with a
        message insisting the file was the wrong type when it was not, and the
        caller had no way to act on it.
        """
        honest = SimpleUploadedFile(
            "contract.pdf", b"%PDF-1.7\nbody", content_type="application/octet-stream",
        )
        self.assertIs(validate_document(honest), honest)

    def test_a_mislabelled_file_is_refused_on_its_contents(self):
        """The false-acceptance. The header is caller-chosen, so claiming
        application/pdf used to be enough to have anything stored unread."""
        with self.assertRaises(DRFValidationError):
            validate_document(SimpleUploadedFile(
                "payload.pdf", b"MZ\x90\x00executable", content_type="application/pdf",
            ))

    def test_the_message_names_the_type_it_actually_found(self):
        """Repeating the whitelist is useless to someone who believes they
        already sent one of those."""
        with self.assertRaises(DRFValidationError) as caught:
            validate_image(SimpleUploadedFile(
                "scan.png", b"%PDF-1.4 really a pdf", content_type="image/png",
            ))
        self.assertIn("PDF", str(caught.exception))

    def test_every_accepted_format_is_recognised_by_signature(self):
        """Each of the four, or the gate silently narrows to whatever is tested."""
        for content_type in uploads.DOCUMENT_TYPES:
            with self.subTest(content_type=content_type):
                f = SimpleUploadedFile(
                    "f", self.MAGIC[content_type], content_type="application/octet-stream",
                )
                self.assertIs(validate_document(f), f)

    def test_the_handle_is_rewound_after_sniffing(self):
        """The storage backend writes from wherever the cursor is left, so a
        probe that does not rewind puts a TRUNCATED object in R2 — invisible to
        any test that only checks the upload was accepted."""
        f = SimpleUploadedFile("a.pdf", b"%PDF-1.4 full body here", content_type="application/pdf")
        validate_document(f)
        self.assertEqual(f.read(), b"%PDF-1.4 full body here")

    def test_an_empty_value_is_let_through(self):
        """Clearing a field is not an upload."""
        self.assertIsNone(validate_photo(None))
        self.assertEqual(validate_photo(""), "")

    # ── the ceilings themselves ─────────────────────────────────────────

    def test_the_ceilings_are_ordered_and_fit_the_worker_timeout(self):
        """The numbers come from the Procfile (`--timeout 120`), not from
        roundness. What spends that budget is client UPSTREAM bandwidth: on a
        congested mobile link (~1 Mbps) a request has 120s x 0.125 MB/s = 15MB
        before the worker is killed, so a client-facing ceiling above that
        cannot complete from a bad connection at all. The larger ceiling is
        reserved for staff-only fields, uploaded from a desk.
        """
        self.assertLess(uploads.MAX_PHOTO_SIZE, uploads.MAX_IMAGE_SIZE)
        self.assertLess(uploads.MAX_IMAGE_SIZE, uploads.MAX_DOCUMENT_SIZE)
        # The client-facing ceiling must stay inside what a phone on a poor
        # connection can upload before --timeout 120 kills the worker.
        self.assertLessEqual(uploads.MAX_IMAGE_SIZE, 15 * uploads.MB)

    def test_every_file_field_in_the_project_is_accounted_for(self):
        """The regression guard, and the reason this test is worth more than the
        others: a NEW FileField added later gets no validator by default and
        nothing would say so. This fails the moment one appears, which is the
        prompt to decide its ceiling rather than inherit "unlimited".
        """
        found = {
            f"{model._meta.label}.{field.name}"
            for model in django_apps.get_models()
            for field in model._meta.get_fields()
            if isinstance(field, models.FileField)  # ImageField subclasses it
        }
        self.assertEqual(found, {
            "contacts.EventContact.photo",
            "portal.TeamMember.photo",
            "events.Event.featured_image",
            "events.EventDay.event_images",
            "budgets.BudgetPayment.receipt",
            "meetings.PrepItemFileUpload.file",
            "document_hub.ClientDocument.file",
            "document_hub.Invoice.file",
            "document_hub.Receipt.file",
            "document_hub.PortalDefaults.service_agreement_file",
            "document_hub.PortalDefaults.welcome_booklet_file",
            "document_hub.PortalDefaults.faq_file",
        })


class UploadCeilingIsWiredToSerializersTests(TestCase):
    """The validator working is not the same as the validator running.

    apps/core/uploads.py could be perfect and every field still unprotected, so
    these go through the serializers themselves — the layer a request actually
    hits — rather than calling the helper directly.
    """

    def _oversized_png(self, size):
        """A REAL png, then an inflated .size.

        It has to be a real image: DRF's ImageField runs Pillow in
        to_internal_value, which is *before* validate_<field>, so junk bytes
        would be rejected for the wrong reason and the test would pass without
        the ceiling existing at all.
        """
        buf = io.BytesIO()
        Image.new("RGB", (1, 1)).save(buf, format="PNG")
        f = SimpleUploadedFile("cover.png", buf.getvalue(), content_type="image/png")
        f.size = size
        return f

    def test_event_featured_image_is_capped(self):
        serializer = EventSerializer(
            data={"featured_image": self._oversized_png(uploads.MAX_IMAGE_SIZE + 1)},
            partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("featured_image", serializer.errors)

    def test_event_day_images_are_capped(self):
        serializer = EventDaySerializer(
            data={"event_images": self._oversized_png(uploads.MAX_IMAGE_SIZE + 1)},
            partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("event_images", serializer.errors)

    def test_client_document_file_is_capped(self):
        """A FileField, so nothing else was checking it at all — not size, not
        type. This is the field a signed contract lands on."""
        oversized = SimpleUploadedFile("deck.pdf", b"%PDF-1.4\n", content_type="application/pdf")
        oversized.size = uploads.MAX_DOCUMENT_SIZE + 1
        serializer = ClientDocumentSerializer(data={"file": oversized}, partial=True)
        self.assertFalse(serializer.is_valid())
        self.assertIn("file", serializer.errors)

    def test_a_document_of_the_wrong_type_is_refused(self):
        """The type half. `.exe` on a FileField used to be a valid upload."""
        binary = SimpleUploadedFile(
            "payload.exe", b"MZ", content_type="application/x-msdownload",
        )
        serializer = ClientDocumentSerializer(data={"file": binary}, partial=True)
        self.assertFalse(serializer.is_valid())
        self.assertIn("file", serializer.errors)

    def test_a_normal_upload_still_passes(self):
        """The ceilings must not refuse the thing they exist to permit."""
        ordinary = self._oversized_png(2 * uploads.MB)
        serializer = EventSerializer(data={"featured_image": ordinary}, partial=True)
        serializer.is_valid()
        self.assertNotIn("featured_image", serializer.errors)


class DatabaseConnectionSettingsTests(TestCase):
    """
    Two Neon-shaped invariants that several docs already depend on.

    Both were previously true only by accident — one by Django's default, one not
    at all — while ADR-0001, RUNBOOK and apps/core/background.py all described
    them as deliberate. These assert the settings rather than the behaviour,
    because the behaviour they guard cannot be reproduced here: the suite runs
    against Neon's DIRECT endpoint, and both problems only appear through the
    `-pooler` host that production uses.
    """

    def _default(self):
        # connections[...] rather than settings.DATABASES: Django normalises the
        # dict on the connection wrapper, so this is the value the backend will
        # actually read.
        from django.db import connections
        return connections["default"].settings_dict

    def test_server_side_cursors_are_disabled(self):
        """PgBouncer in transaction-pooling mode returns the connection to the
        pool between statements, so a server-side cursor is gone by the time it
        is fetched from — QuerySet.iterator() raises "cursor does not exist".

        The reason this needs a test rather than a comment: the failure is
        environment-split. Against the direct endpoint this suite uses,
        .iterator() works, so a bulk sweep using it would pass CI and break only
        in production.
        """
        self.assertIs(self._default()["DISABLE_SERVER_SIDE_CURSORS"], True)

    def test_connections_are_not_persistent(self):
        """Neon bills idle compute and autosuspends at ~5 min. A connection held
        open across requests defeats that, which is the same bill that removing
        Celery's beat was about (docs/adr/0001-remove-celery.md).

        Django's default is already 0; this pins it so raising it is a failing
        test rather than a silent quota burn discovered on an invoice.
        """
        self.assertEqual(self._default()["CONN_MAX_AGE"], 0)


class ThrottleNearLimitSignalTests(TestCase):
    """
    The occupancy signal. A block is a lagging indicator — it only appears once
    somebody has already been refused, which is too late to tell you a ceiling
    is wrong. This fires before the bucket fills, and costs no extra cache read
    because the throttle already holds its history when it decides.
    """

    def _throttle(self, used, limit=10):
        throttle = UserBurstRateThrottle()
        throttle.rate = f"{limit}/m"
        throttle.num_requests, throttle.duration = throttle.parse_rate(throttle.rate)
        throttle.key = "throttle_user_burst_test"
        throttle.history = [0.0] * used
        throttle.now = 0.0
        return throttle

    def test_it_stays_quiet_below_the_threshold(self):
        # 4th of 10 with a 0.8 threshold — well clear.
        with self.assertNoLogs("apps.core.throttling", level="WARNING"):
            self._throttle(used=3).throttle_success()

    def test_it_fires_at_the_threshold(self):
        # 8th of 10 == 0.8 exactly, which must count as "near".
        with self.assertLogs("apps.core.throttling", level="WARNING") as captured:
            self._throttle(used=7).throttle_success()
        self.assertIn("throttle_near_limit", str(captured.records[0].__dict__))

    @override_settings(THROTTLE_NEAR_LIMIT_FRACTION=0.2)
    def test_the_threshold_is_tunable_without_a_code_change(self):
        with self.assertLogs("apps.core.throttling", level="WARNING"):
            self._throttle(used=1).throttle_success()  # 2nd of 10 = 0.2


class UserBurstAnonymousFallbackTests(TestCase):
    """
    The per-account ceiling must not meter anonymous callers.

    ``UserRateThrottle`` ships a fallback that buckets an unauthenticated request
    by ``get_ident(request)``. Because these throttles are project DEFAULTS they
    run on the anonymous endpoints too, so that fallback quietly reintroduced
    three fixed defects on the ``user_burst`` scope: a prepended
    ``X-Forwarded-For`` gave a fresh bucket, the proxy's own address sat in the
    key, and with no XFF at all every anonymous caller shared one 120/m bucket.
    """

    def _anon(self, xff=None, remote="10.0.0.5"):
        extra = {"REMOTE_ADDR": remote}
        if xff:
            extra["HTTP_X_FORWARDED_FOR"] = xff
        request = APIRequestFactory().post("/api/v1/auth/token/", {}, format="json", **extra)
        request.user = AnonymousUser()
        return request

    def test_an_anonymous_request_gets_no_per_account_bucket(self):
        throttle = UserBurstRateThrottle()
        for label, request in [
            ("normal", self._anon("41.2.3.4, 10.0.0.5")),
            ("spoofed", self._anon("9.9.9.9, 41.2.3.4, 10.0.0.5")),
            ("no xff", self._anon()),
        ]:
            self.assertIsNone(throttle.get_cache_key(request, None), label)

    def test_an_account_holder_still_gets_one(self):
        request = self._anon("41.2.3.4, 10.0.0.5")
        request.user = get_user_model()(email="x@example.com")
        request.user.id = uuid.uuid4()

        key = UserBurstRateThrottle().get_cache_key(request, None)
        self.assertIsNotNone(key)
        self.assertIn(str(request.user.pk), key)

    def test_no_throttle_in_the_project_consults_drf_num_proxies(self):
        """NUM_PROXIES only ever reaches DRF's own BaseThrottle.get_ident. Every
        throttle here overrides that, so the setting is dead weight — pinned so
        nobody spends time configuring it, and so a future throttle added without
        the override is caught here instead of in production."""
        from django.utils.module_loading import import_string
        from rest_framework.throttling import BaseThrottle

        for path in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]:
            throttle_cls = import_string(path)
            self.assertIsNot(
                throttle_cls.get_ident, BaseThrottle.get_ident,
                f"{path} inherits DRF's spoofable get_ident",
            )


class BackgroundTaskDispatchTests(TestCase):
    """
    apps/core/background is the whole of what replaced the Celery broker, so the
    properties below are load-bearing rather than incidental. Each test names the
    failure it exists to prevent.
    """

    def setUp(self):
        from apps.core import background
        self.background = background
        self.addCleanup(background.disable_async)
        self.addCleanup(background.shutdown)

    def _task(self, fn):
        from apps.core.background import background_task
        return background_task(name="tests.probe")(fn)

    # -- inline mode -------------------------------------------------------

    def test_delay_runs_inline_when_async_is_not_enabled(self):
        """The reason async is opt-in per process. `retry_failed_notifications`
        dispatches sends from inside a cron process that exits seconds later —
        handing them to a pool there would silently drop exactly the mail the
        sweep exists to rescue."""
        ran = []
        task = self._task(lambda: ran.append(threading.current_thread().name))

        task.delay()

        self.assertEqual(len(ran), 1)
        self.assertEqual(ran[0], threading.current_thread().name)

    @override_settings(BACKGROUND_EAGER=True)
    def test_eager_forces_inline_even_in_the_web_process(self):
        self.background.enable_async()
        ran = []
        task = self._task(lambda: ran.append(threading.current_thread().name))

        task.delay()

        self.assertEqual(ran, [threading.current_thread().name])

    # -- failure handling --------------------------------------------------

    def test_a_raising_task_is_logged_not_silently_lost(self):
        """A bare thread that raises disappears without a trace — strictly worse
        than Celery, which at least logged it."""
        task = self._task(lambda: 1 / 0)

        with self.assertLogs("apps.core.background", level="ERROR") as captured:
            task.delay()  # must not propagate

        self.assertTrue(any("background task failed" in line for line in captured.output))

    def test_apply_captures_failure_instead_of_raising(self):
        """run_scheduled runs a whole group and reports every failure at the end;
        one broken task must not strand the rest."""
        task = self._task(lambda: 1 / 0)

        result = task.apply()

        self.assertFalse(result.successful())
        self.assertIsInstance(result.result, ZeroDivisionError)
        with self.assertRaises(ZeroDivisionError):
            result.get()

    def test_calling_the_task_directly_still_raises(self):
        task = self._task(lambda: 1 / 0)
        with self.assertRaises(ZeroDivisionError):
            task()


class BackgroundPoolTests(TestCase):
    """
    The async half of apps/core/background.

    Async dispatch goes through `transaction.on_commit`, and TestCase wraps each
    test in a transaction it rolls back — so the callbacks never fire on their
    own. `captureOnCommitCallbacks(execute=True)` runs them deliberately, which
    doubles as the assertion that on_commit is what dispatch actually used: a
    callback that was never registered there cannot be captured.

    TransactionTestCase would also work and was the first attempt. It is the
    wrong tool here: it TRUNCATEs tables between tests and does not restore
    data seeded by migrations, so the seeded `ServiceHealthState` ("brevo") and
    the NotificationTypeSettings / ScheduledTaskSettings rows vanished for every
    class that ran afterwards — four unrelated notification tests started failing
    only in a full-suite run.

    Consequence to keep in mind when adding to this class: the worker thread gets
    its own database connection and therefore cannot see this test's uncommitted
    rows. Task bodies here must not depend on data the test created.
    """

    def setUp(self):
        from apps.core import background
        self.background = background
        self.addCleanup(background.disable_async)
        self.addCleanup(background.shutdown)

    def _task(self, fn):
        from apps.core.background import background_task
        return background_task(name="tests.probe")(fn)

    def _drain(self, timeout=10):
        """Wait for the pool to report nothing in flight."""
        import time
        deadline = time.monotonic() + timeout
        while self.background.inflight() and time.monotonic() < deadline:
            time.sleep(0.01)

    @override_settings(BACKGROUND_EAGER=False)
    def test_async_dispatch_runs_on_another_thread_and_closes_its_connection(self):
        """A leaked thread-local connection holds a serverless Postgres awake and
        defeats the autosuspend that removing beat exists to enable."""
        from django.db import connection

        self.background.enable_async()
        done = threading.Event()
        observed = {}

        def body():
            # Touch the DB so this thread definitely opens a connection.
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
            observed["thread"] = threading.current_thread().name
            observed["usable"] = connection.connection is not None
            done.set()

        task = self._task(body)
        with self.captureOnCommitCallbacks(execute=True):
            task.delay()

        self.assertTrue(done.wait(timeout=10))
        self._drain()
        self.assertNotEqual(observed["thread"], threading.current_thread().name)
        self.assertTrue(observed["usable"])  # it really did open one
        self.assertEqual(self.background.inflight(), 0)

    @override_settings(BACKGROUND_EAGER=False)
    def test_dispatch_waits_for_commit(self):
        """A thread must never race ahead of the row it describes. Several
        queue_notification callers run inside transaction.atomic(); Celery's
        network hop usually lost that race for us, a thread would not."""
        from django.db import transaction

        self.background.enable_async()
        started = threading.Event()
        task = self._task(started.set)

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with transaction.atomic():
                task.delay()
                # Still inside the transaction: nothing may have run yet.
                self.assertFalse(started.is_set())
                # The slot is reserved at dispatch, but the work is not queued.
                self.assertEqual(self.background.inflight(), 1)

        # Exactly one on_commit callback — the pool submit. If dispatch had run
        # the work immediately there would be none to capture.
        self.assertEqual(len(callbacks), 1)
        self.assertTrue(started.wait(timeout=10))
        self._drain()

    @override_settings(BACKGROUND_EAGER=False, BACKGROUND_MAX_QUEUED=0)
    def test_backpressure_degrades_to_inline_rather_than_dropping(self):
        """apps/inquiries fans out one notification per flagged staff member on a
        public unauthenticated endpoint. Full pool must mean slower, not lossy."""
        self.background.enable_async()
        ran = []
        task = self._task(lambda: ran.append(threading.current_thread().name))

        task.delay()

        self.assertEqual(ran, [threading.current_thread().name])

    # -- correlation id ----------------------------------------------------

    @override_settings(BACKGROUND_EAGER=False)
    def test_the_request_id_contextvar_reaches_the_worker_thread(self):
        """ThreadPoolExecutor does not propagate contextvars. Without the
        copy_context() every background log line would lose the X-Request-ID
        that the Celery signal handlers used to carry across the broker."""
        from apps.core.middleware import request_id_var

        self.background.enable_async()
        seen = {}
        done = threading.Event()

        def body():
            seen["rid"] = request_id_var.get()
            done.set()

        task = self._task(body)
        token = request_id_var.set("rid-abc123")
        try:
            with self.captureOnCommitCallbacks(execute=True):
                task.delay()
        finally:
            request_id_var.reset(token)

        self.assertTrue(done.wait(timeout=10))
        self._drain()
        self.assertEqual(seen["rid"], "rid-abc123")


class RunScheduledGroupTests(TestCase):
    """
    The GROUPS table is the whole schedule now — there is no beat and no
    PeriodicTask row. A typo in a dotted path is a task that silently never runs,
    so resolve every one of them.
    """

    def test_every_task_path_resolves_to_a_background_task(self):
        from apps.core.background import BackgroundTask
        from apps.core.management.commands.run_scheduled import GROUPS, _resolve

        for group, entries in GROUPS.items():
            for label, path in entries:
                with self.subTest(group=group, label=label):
                    self.assertIsInstance(_resolve(path), BackgroundTask)

    def test_no_task_is_scheduled_in_two_groups(self):
        """Two cron services running the same sweep is a double-send waiting to
        happen."""
        from apps.core.management.commands.run_scheduled import GROUPS

        paths = [path for entries in GROUPS.values() for _, path in entries]
        self.assertEqual(len(paths), len(set(paths)))

    def test_the_retry_sweep_is_in_the_short_cadence_group(self):
        """It is the only retry path for a failed send, and a password-reset code
        expires in RESET_CODE_TTL_MINUTES — so it must not drift into a daily
        group."""
        from apps.core.management.commands.run_scheduled import GROUPS

        self.assertIn(
            "apps.notifications.tasks:retry_failed_notifications_task",
            [path for _, path in GROUPS["notification_retry"]],
        )

    def test_an_unknown_group_is_rejected(self):
        from django.core.management import CommandError, call_command

        with self.assertRaises(CommandError):
            call_command("run_scheduled", "not_a_group")


class RecipientTimezoneTests(TestCase):
    """
    P2-9. `PaymentMilestone.due_date` and `Meeting.date` are naive DateFields — a
    calendar day where the CLIENT is. Comparing them against `timezone.now().date()`
    compares them against UTC's day, which is a different day for anyone far enough
    from UTC. See apps/core/timezones.py.
    """

    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            first_name="Zone", last_name="Target", email="tz@example.com", password="x",
        )

    def test_a_blank_timezone_inherits_the_platform_default(self):
        from apps.core.timezones import user_timezone

        self.user.timezone = ""
        with override_settings(PLATFORM_DEFAULT_TIMEZONE="Africa/Lagos"):
            self.assertEqual(str(user_timezone(self.user)), "Africa/Lagos")

    def test_an_account_timezone_wins_over_the_default(self):
        from apps.core.timezones import user_timezone

        self.user.timezone = "America/New_York"
        with override_settings(PLATFORM_DEFAULT_TIMEZONE="Africa/Lagos"):
            self.assertEqual(str(user_timezone(self.user)), "America/New_York")

    def test_an_unknown_timezone_degrades_to_utc_and_is_logged(self):
        """Fail-soft on read: one account with a bad value must not take out a
        whole digest run. It is caught on write instead (clean()/serializer)."""
        from apps.core.timezones import _zone_cache, resolve_timezone_name

        _zone_cache.pop("Mars/Olympus_Mons", None)
        with self.assertLogs("apps.core.timezones", level="ERROR") as captured:
            zone = resolve_timezone_name("Mars/Olympus_Mons")

        self.assertEqual(str(zone), "UTC")
        self.assertTrue(any("unknown timezone" in line for line in captured.output))

    def test_the_model_rejects_an_unknown_timezone_on_save(self):
        from django.core.exceptions import ValidationError

        self.user.timezone = "Mars/Olympus_Mons"
        with self.assertRaises(ValidationError):
            self.user.full_clean()

    def test_a_real_timezone_passes_validation(self):
        self.user.timezone = "Pacific/Auckland"
        self.user.full_clean()  # must not raise

    def test_the_serializer_rejects_an_unknown_timezone(self):
        from apps.accounts.serializers import UserUpdateSerializer

        serializer = UserUpdateSerializer(
            self.user, data={"timezone": "Mars/Olympus_Mons"}, partial=True
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("timezone", serializer.errors)

    def test_the_serializer_accepts_blank_to_mean_platform_default(self):
        from apps.accounts.serializers import UserUpdateSerializer

        serializer = UserUpdateSerializer(self.user, data={"timezone": ""}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_local_today_differs_across_the_dateline(self):
        """The bug, in one assertion. At 23:30 UTC two clients are on different
        calendar days, and a UTC-derived `today` is wrong for one of them."""
        from unittest.mock import patch

        from apps.core.timezones import local_today

        instant = datetime.datetime(2026, 10, 10, 23, 30, tzinfo=datetime.timezone.utc)

        # UserManager.create_user takes a fixed signature, so timezone is set
        # afterwards — which is also how it works in practice: an account is
        # created with a blank timezone (inheriting the platform default) and
        # sets its own later via PATCH /users/me/update/ or the admin.
        east = self.User.objects.create_user(
            first_name="E", last_name="Z", email="east@example.com", password="x",
        )
        east.timezone = "Pacific/Auckland"   # UTC+13 in October
        east.save(update_fields=["timezone"])

        west = self.User.objects.create_user(
            first_name="W", last_name="Z", email="west@example.com", password="x",
        )
        west.timezone = "Pacific/Midway"     # UTC-11 year round
        west.save(update_fields=["timezone"])

        with patch("django.utils.timezone.now", return_value=instant):
            self.assertEqual(local_today(east), datetime.date(2026, 10, 11))
            self.assertEqual(local_today(west), datetime.date(2026, 10, 10))

    def test_the_maximum_skew_is_one_day(self):
        """What the digests widen their queries by. Real offsets run UTC-12..UTC+14,
        so a local date is never two days from the UTC date."""
        from apps.core.timezones import max_utc_offset_days

        self.assertEqual(max_utc_offset_days(), 1)


@override_settings(RATELIMIT_ENABLE=True)
class RetryAfterReportsTheRealWaitTests(TestCase):
    """
    The 429's `Retry-After` used to be a flat 60 on every django-ratelimit
    block, which is a lie on a daily cap — a client that respects the header
    retries every minute for up to 24 hours and is refused every time.

    Fixed by carrying `time_left` to the renderer: either on the exception (for
    a caller that raises its own, like login) or by re-checking the tiers the
    `_rl` helpers stash on the request.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _post(self, url, client_ip="41.2.3.4", **body):
        return self.client.post(
            url, data=body, content_type="application/json",
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_FOR=f"{client_ip}, 10.0.0.5",
        )

    def test_a_per_minute_block_reports_about_a_minute(self):
        """Not the flat constant by coincidence — a minute limit really does
        clear within a minute, so this pins the shape rather than the number."""
        url = reverse("password_reset_verify")
        limit, _ = _split_rate(settings.RATE_LIMITS["password_reset_verify"])
        for attempt in range(limit):
            self._post(url, email="a@example.com", code="000000")

        blocked = self._post(url, email="a@example.com", code="000000")
        self.assertEqual(blocked.status_code, 429)
        retry = int(blocked.headers["Retry-After"])
        self.assertGreater(retry, 0)
        self.assertLessEqual(retry, 60)

    def test_a_daily_block_reports_hours_not_sixty_seconds(self):
        """The failure this fixes. Driven to the DAILY ceiling, so the honest
        answer is hours — a flat 60 would send the caller back 1,440 times."""
        url = reverse("password_reset_confirm")
        daily, _ = _split_rate(settings.RATE_LIMITS["password_reset_confirm_daily"])

        # The per-minute tier is 10 and the day is 20, so spread the attempts
        # across enough source addresses that only the (IP-keyed) day fills.
        # Same IP for the day, fresh minute windows via the clock.
        blocked = None
        with patch("django_ratelimit.core.time.time") as clock:
            for attempt in range(daily + 1):
                clock.return_value = 1_700_000_000.0 + attempt * 61
                blocked = self._post(
                    url, email="a@example.com", code="000000",
                    new_password="Sw0rdfish!23",
                )

        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(int(blocked.headers["Retry-After"]), 3600)

    def test_the_largest_wait_wins_when_two_tiers_are_full(self):
        """Reporting the burst's seconds while the day is also full guarantees
        the next attempt is refused too."""
        url = reverse("password_reset_confirm")
        daily, _ = _split_rate(settings.RATE_LIMITS["password_reset_confirm_daily"])

        with patch("django_ratelimit.core.time.time") as clock:
            for attempt in range(daily + 1):
                clock.return_value = 1_700_000_000.0 + attempt * 61
                self._post(url, email="a@example.com", code="000000",
                           new_password="Sw0rdfish!23")
            # Now hammer the same second so the per-minute tier fills too.
            clock.return_value = 1_700_000_000.0 + (daily + 1) * 61
            blocked = self._post(url, email="a@example.com", code="000000",
                                 new_password="Sw0rdfish!23")

        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(int(blocked.headers["Retry-After"]), 3600)

    def test_login_carries_its_wait_on_the_exception(self):
        """Login checks the tiers itself and raises its own Ratelimited, so it
        knows the answer and does not need the request-stash path."""
        url = reverse("token_obtain_pair")
        limit, _ = _split_rate(settings.RATE_LIMITS["auth_login"])
        for attempt in range(limit):
            self._post(url, email=f"x{attempt}@example.com", password="wrong")

        blocked = self._post(url, email="x-last@example.com", password="wrong")
        self.assertEqual(blocked.status_code, 429)
        retry = int(blocked.headers["Retry-After"])
        self.assertGreater(retry, 0)
        self.assertLessEqual(retry, 60)

    def test_the_header_is_never_zero_or_negative(self):
        """A window can close between the block and the render; Retry-After: 0
        is an invite to retry immediately and be refused again."""
        url = reverse("password_reset_verify")
        limit, _ = _split_rate(settings.RATE_LIMITS["password_reset_verify"])
        for _ in range(limit):
            self._post(url, email="a@example.com", code="000000")
        blocked = self._post(url, email="a@example.com", code="000000")
        self.assertGreaterEqual(int(blocked.headers["Retry-After"]), 1)

    def test_the_tiers_are_stashed_on_the_request_by_the_rl_helper(self):
        """The mechanism the recompute path depends on. Stashed on the REQUEST,
        not the view function: resolve() on /inquiries/ returns the dispatcher,
        not the wrapped POST handler."""
        from apps.inquiries.urls import _submit_inquiry

        seen = {}

        class _Req:
            method = "POST"
            META = {"REMOTE_ADDR": "127.0.0.1"}
            body = b"{}"

        request = _Req()
        try:
            _submit_inquiry(request)
        except Exception:  # the view itself will fail on a fake request
            pass
        seen["tiers"] = getattr(request, "rate_limit_tiers", None)

        self.assertIsNotNone(seen["tiers"])
        self.assertEqual(
            [group for group, _, _ in seen["tiers"]],
            ["inquiry_submit_burst", "inquiry_submit_ip"],
        )


@override_settings(BACKGROUND_EAGER=False)
class ScheduleInTests(TestCase):
    """
    `BackgroundTask.schedule_in` — opportunistic precision on top of a durable
    row (docs/adr/0001-remove-celery.md).

    It must never become the guarantee: everything here checks that failing to
    arm, or arming twice, costs punctuality and nothing else.

    `BACKGROUND_EAGER` is forced True under the test runner, which makes
    `async_enabled()` False and `schedule_in` a no-op — the right default, since
    the suite drives the sweep directly and no test should ever wait out a real
    debounce. These opt back in to exercise the arming path at all.
    """

    def setUp(self):
        background.cancel_timers()
        self.addCleanup(background.cancel_timers)
        self.addCleanup(background.disable_async)
        self.calls = []

        @background.background_task(name="test.timed")
        def _task(marker):
            self.calls.append(marker)

        self.task = _task

    def test_it_is_a_no_op_outside_the_web_process(self):
        """The critical difference from .delay(), which degrades to INLINE.
        A delayed task run inline would block a cron run for the whole delay."""
        background.disable_async()
        self.assertFalse(self.task.schedule_in(600, "x", key="k"))
        self.assertEqual(background.armed_timers(), 0)
        self.assertEqual(self.calls, [])

    def test_it_arms_in_the_web_process(self):
        background.enable_async()
        self.assertTrue(self.task.schedule_in(600, "x", key="k"))
        self.assertEqual(background.armed_timers(), 1)

    def test_re_arming_the_same_key_replaces_rather_than_stacks(self):
        """A debounce re-stamped on every edit would otherwise leave one
        sleeping thread per edit."""
        background.enable_async()
        for _ in range(20):
            self.task.schedule_in(600, "x", key="same")
        self.assertEqual(background.armed_timers(), 1)

    def test_different_keys_arm_independently(self):
        background.enable_async()
        self.task.schedule_in(600, "a", key="a")
        self.task.schedule_in(600, "b", key="b")
        self.assertEqual(background.armed_timers(), 2)

    @override_settings(BACKGROUND_MAX_TIMERS=3)
    def test_the_cap_declines_to_arm_rather_than_dropping_work(self):
        """Refusing to arm costs precision, never correctness — the sweep still
        delivers. That is what makes a hard cap safe here."""
        background.enable_async()
        armed = [self.task.schedule_in(600, i, key=f"k{i}") for i in range(6)]
        self.assertEqual(armed, [True, True, True, False, False, False])
        self.assertEqual(background.armed_timers(), 3)

    def test_the_task_actually_runs_when_the_timer_fires(self):
        background.enable_async()
        self.task.schedule_in(0.05, "fired", key="k")
        deadline = time.monotonic() + 5
        while not self.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.calls, ["fired"])
        self.assertEqual(background.armed_timers(), 0)

    def test_a_failing_task_does_not_kill_the_timer_thread_silently(self):
        """Same contract as the pool: a thread that raises disappears without a
        trace, which is strictly worse than what Celery did."""
        background.enable_async()

        @background.background_task(name="test.boom")
        def _boom():
            raise RuntimeError("boom")

        with self.assertLogs("apps.core.background", level="ERROR"):
            _boom.schedule_in(0.05, key="boom")
            deadline = time.monotonic() + 5
            while background.armed_timers() and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.05)

    def test_cancelling_prevents_the_run(self):
        background.enable_async()
        self.task.schedule_in(0.2, "should-not-run", key="k")
        background.cancel_timers()
        time.sleep(0.3)
        self.assertEqual(self.calls, [])


class EventCatalogueTests(TestCase):
    """docs/observability/README.md claims to list every `event=` slug the
    codebase emits, "grep-verified against the source, not aspirational".

    Nothing enforced that claim, and it drifted: six reCAPTCHA events shipped
    undocumented and therefore unalerted, which is the failure mode the
    catalogue exists to prevent. A missing entry is not a documentation nit —
    the README is where an event gets triaged into P1/P2/P3, so an event that
    never lands in it is one nobody ever decided whether to alert on.

    Forward direction only: emitted-but-undocumented is the drift that actually
    happens, because adding a logger call and adding a table row are separate
    edits. The reverse (a stale entry for an event that was deleted) is left
    unchecked, since the README's backticks also hold filenames, field names and
    settings keys, and telling those apart from slugs needs a heuristic that
    would itself go stale.
    """

    EVENT_LITERAL = re.compile(r'"event"\s*:\s*"([a-z0-9_]+)"')
    SOURCE_ROOTS = ("apps", "config")

    def _emitted_events(self):
        """Every slug passed as extra={"event": ...} in application code.

        tests.py is excluded deliberately: a test asserting on an event slug is
        not an emission, and requiring one to be documented would make this test
        fire on its own fixtures.
        """
        found = {}
        for root in self.SOURCE_ROOTS:
            for path in (settings.BASE_DIR / root).rglob("*.py"):
                if path.name == "tests.py" or "__pycache__" in path.parts:
                    continue
                for slug in self.EVENT_LITERAL.findall(path.read_text()):
                    found.setdefault(slug, path)
        return found

    def test_every_emitted_event_is_in_the_catalogue(self):
        readme = settings.BASE_DIR / "docs" / "observability" / "README.md"
        text = readme.read_text()

        emitted = self._emitted_events()
        self.assertTrue(emitted, "found no event literals at all — the regex has rotted")

        # Backtick-delimited so `rate_limited` cannot be satisfied by
        # `admin_login_rate_limited` merely containing it.
        missing = {
            slug: path for slug, path in emitted.items() if f"`{slug}`" not in text
        }
        self.assertEqual(
            missing,
            {},
            "These events are emitted but absent from docs/observability/README.md, "
            "so nobody has decided whether they page, notify, or sit on a dashboard. "
            "Add each to the P1/P2/P3 catalogue (and a rule to "
            "grafana-alert-rules.yaml if it earns one): "
            + ", ".join(
                f"{slug} ({path.relative_to(settings.BASE_DIR)})"
                for slug, path in sorted(missing.items())
            ),
        )
