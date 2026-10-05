"""
apps/core/test_public_endpoints.py

REST_FRAMEWORK's DEFAULT_PERMISSION_CLASSES is IsAuthenticated (default-deny).
This walks every URL pattern and pins the exact set reachable without
authentication, so a new public endpoint has to be added here on purpose, and a
view that forgets @permission_classes fails closed instead of open.
"""

from django.test import SimpleTestCase, TestCase
from django.urls import URLResolver, get_resolver
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.test import APIClient
from rest_framework.views import APIView

# DRF views whose permission_classes are empty or AllowAny-only.
PUBLIC_DRF_ROUTES = {
    "api/v1/public/home-strip/",
    "api/v1/auth/token/",
    "api/v1/auth/token/refresh/",
    "api/v1/auth/password-reset/request/",
    "api/v1/auth/password-reset/verify/",
    "api/v1/auth/password-reset/confirm/",
    "api/v1/portfolio/events/",
    "api/v1/portfolio/events/<slug:slug>/",
}

# Plain Django views (DRF permissions do not apply). Each guards itself or is
# meant to be open; listed so a new one cannot appear unnoticed.
NON_DRF_ROUTES = {
    "health/",  # liveness probe, no auth by design
    "health/ready/",  # readiness probe, no auth by design
    "admin-files/<str:file_type>/<str:obj_id>/",  # session auth + staff check inside
    "api/v1/inquiries/",  # dispatcher: public POST submit_inquiry / staff-only GET list_inquiries
}


def _walk(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, prefix + str(p.pattern))
        else:
            yield prefix + str(p.pattern), p.callback


def _routes():
    for route, callback in _walk(get_resolver().url_patterns):
        if route.startswith("admin/"):
            continue  # Django admin: its own session + is_staff gate
        yield route, callback


class PublicEndpointInventoryTests(SimpleTestCase):
    def test_default_permission_is_authenticated(self):
        self.assertEqual(APIView.permission_classes, [IsAuthenticated])

    def test_the_public_drf_set_is_exactly_the_intended_list(self):
        public, non_drf = set(), set()
        for route, callback in _routes():
            cls = getattr(callback, "cls", None)
            if cls is None or not issubclass(cls, APIView):
                non_drf.add(route)
                continue
            perms = list(cls.permission_classes)
            if not perms or all(issubclass(p, AllowAny) for p in perms):
                public.add(route)

        self.assertEqual(public, PUBLIC_DRF_ROUTES)
        self.assertEqual(non_drf, NON_DRF_ROUTES)

    def test_inquiry_dispatcher_branches_keep_their_permissions(self):
        from apps.inquiries import views

        self.assertEqual(list(views.submit_inquiry.cls.permission_classes), [])
        self.assertIn(IsAuthenticated, views.list_inquiries.cls.permission_classes)


class PublicEndpointsAnswerAnonymouslyTests(TestCase):
    def setUp(self):
        self.api = APIClient()

    def test_public_reads_are_open(self):
        for path in ("/health/", "/api/v1/public/home-strip/", "/api/v1/portfolio/events/"):
            with self.subTest(path=path):
                self.assertEqual(self.api.get(path).status_code, 200)

    def test_inquiry_submit_is_open(self):
        # An empty body fails validation (400), which is past the permission check.
        self.assertEqual(self.api.post("/api/v1/inquiries/", {}, format="json").status_code, 400)

    def test_removed_debug_endpoints_are_gone(self):
        self.assertEqual(self.api.get("/api/v1/").status_code, 404)
        self.assertEqual(self.api.get("/api/v1/secure/").status_code, 404)

    def test_a_protected_endpoint_refuses_anonymous(self):
        self.assertEqual(self.api.get("/api/v1/users/me/").status_code, 401)
