"""
apps/events/test_portfolio_throttle.py

The two public portfolio views draw from their own `portfolio` throttle bucket,
per client IP, not the shared `anon` one that ISR revalidation from shared
Vercel IPs was exhausting.
"""

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import SimpleRateThrottle

from apps.core.ratelimit import bucket_ip
from apps.core.throttling import PortfolioRateThrottle
from apps.events import public_views, views

factory = APIRequestFactory()


class PortfolioThrottleScopeTests(TestCase):
    def test_both_portfolio_views_use_only_the_portfolio_scope(self):
        for view in (public_views.portfolio_events, public_views.portfolio_event_detail):
            with self.subTest(view=view.__name__):
                self.assertEqual(view.cls.throttle_classes, [PortfolioRateThrottle])
        self.assertEqual(PortfolioRateThrottle.scope, "portfolio")

    def test_other_endpoints_keep_the_defaults(self):
        self.assertNotIn(PortfolioRateThrottle, views.get_event.cls.throttle_classes)
        self.assertEqual(
            [f"{c.__module__}.{c.__name__}" for c in views.get_event.cls.throttle_classes],
            settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"],
        )

    def test_declared_rate_is_far_above_anon(self):
        parse = SimpleRateThrottle.parse_rate
        portfolio = parse(None, settings.THROTTLE_RATES["portfolio"])
        anon = parse(None, settings.THROTTLE_RATES["anon"])
        self.assertGreater(portfolio[0] / portfolio[1], 10 * anon[0] / anon[1])

    def test_keyed_on_the_project_client_ip(self):
        req = factory.get("/", HTTP_X_FORWARDED_FOR="9.9.9.9, 41.2.3.4", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(PortfolioRateThrottle().get_ident(req), bucket_ip(req))

    def test_disabled_under_the_test_runner(self):
        self.assertIsNone(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["portfolio"])
        for _ in range(3):
            self.assertEqual(public_views.portfolio_events(factory.get("/")).status_code, 200)

    def test_the_scope_actually_limits_when_a_rate_is_set(self):
        cache.clear()
        # SimpleRateThrottle reads its rates from a class attribute bound at
        # import, so the test runner's nulled rates are swapped out here.
        rates = {**PortfolioRateThrottle.THROTTLE_RATES, "portfolio": "2/min"}
        original = PortfolioRateThrottle.THROTTLE_RATES
        PortfolioRateThrottle.THROTTLE_RATES = rates
        try:
            codes = [public_views.portfolio_events(factory.get("/")).status_code for _ in range(3)]
        finally:
            PortfolioRateThrottle.THROTTLE_RATES = original
            cache.clear()
        self.assertEqual(codes, [200, 200, 429])
