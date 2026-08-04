"""
apps/accounts/urls.py

Auth, token, and user routes. Mounted under the versioned prefix in
config/urls.py, so every path below resolves as ``/api/v1/<path>``.

URL ``name=`` values are kept stable (token_obtain_pair, token_refresh, …) so
reverse() and any existing references keep working across the path cleanup.
Specific ``users/`` paths are listed before the ``users/<email>/`` catch-all so
"me" / "register" are not swallowed as an email.
"""

from django.urls import path
from django_ratelimit.decorators import ratelimit
from rest_framework_simplejwt.views import TokenRefreshView

from apps.core.ratelimit import RATE_LIMITS, client_ip, ip_and_email

from . import views
from .views import (
    ForcePasswordChangeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    PasswordResetVerifyView,
)


def _rl(view, rate, key=client_ip):
    """Wrap a CBV's ``.as_view()`` with django-ratelimit, placed OUTERMOST so the
    Ratelimited exception is raised before DRF's dispatch (where it would be
    converted to a 403) and is instead caught by RatelimitMiddleware → the 429
    RATELIMIT_VIEW. block=True enforces; method='POST' only counts POSTs."""
    return ratelimit(key=key, rate=rate, method="POST", block=True)(view)

urlpatterns = [
    # ── Health ──────────────────────────────────────────────────
    path('', views.Home, name='home'),  # GET
    path('secure/', views.secure, name='secure'),  # GET

    # ── Auth & tokens ───────────────────────────────────────────
    # Public POST endpoints are rate-limited (credential stuffing, code guessing,
    # email-bomb / enumeration). logout + force-password-change are authenticated,
    # so they're left unwrapped.
    path('auth/token/', _rl(views.MyTokenObtainPairView.as_view(), RATE_LIMITS['auth_login']), name='token_obtain_pair'),  # POST
    path('auth/token/refresh/', _rl(TokenRefreshView.as_view(), RATE_LIMITS['token_refresh']), name='token_refresh'),  # POST
    path('auth/token/logout/', views.LogoutView.as_view(), name='token_logout'),  # POST

    path('auth/password-reset/request/', _rl(PasswordResetRequestView.as_view(), RATE_LIMITS['password_reset_request'], key=ip_and_email), name='password_reset_request'),  # POST
    path('auth/password-reset/verify/', _rl(PasswordResetVerifyView.as_view(), RATE_LIMITS['password_reset_verify']), name='password_reset_verify'),  # POST
    path('auth/password-reset/confirm/', _rl(PasswordResetConfirmView.as_view(), RATE_LIMITS['password_reset_confirm']), name='password_reset_confirm'),  # POST

    path('auth/force-password-change/', ForcePasswordChangeView.as_view(), name='force_password_change'),  # POST

    # ── Users ───────────────────────────────────────────────────
    path('users/', views.list_users, name='list_users'),  # GET — staff/admin only
    path('users/me/', views.UserInfo, name='user_info'),  # GET
    path('users/me/update/', views.update_user, name='update_user'),  # PATCH|PUT
    path('users/register/', views.register_user, name='register'),   # POST — staff/admin only
    # Listed before the users/<email>/ catch-all for clarity (the str converter
    # never matches a '/', so this would resolve either way).
    path('users/<str:email>/status/', views.set_user_status, name='set_user_status'),  # PATCH — staff/admin only
    path('users/<str:email>/', views.UserInfowEmail, name='user_info_email'),  # GET
]
