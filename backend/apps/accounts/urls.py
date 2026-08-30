"""
apps/accounts/urls.py

Auth, token, and user routes. Mounted under the versioned prefix in
config/urls.py, so every path below resolves as ``/api/v1/<path>``.

URL ``name=`` values are kept stable (token_obtain_pair, token_refresh, …) so
reverse() and any existing references keep working across the path cleanup.
Specific ``users/`` paths are listed before the ``users/<email>/`` catch-all so
"me" / "register" are not swallowed as an email.
"""

from functools import wraps

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


def _rl(view, *limits):
    """Wrap a CBV's ``.as_view()`` with one or more django-ratelimit decorators.

    Each entry in ``limits`` is ``(group, rate, key)``, applied so the FIRST
    listed ends up outermost. Placement outermost is mandatory: the Ratelimited
    exception must be raised before DRF's dispatch, which would convert it to a
    403, so that RatelimitMiddleware sees it and renders the 429 RATELIMIT_VIEW.
    block=True enforces; method='POST' only counts POSTs.

    ``group`` is explicit and required on every limit — never left to
    django-ratelimit to infer. Inferred, it becomes the view function's
    ``__module__ + __qualname__``, and every ``as_view()`` result carries the
    same qualname (``View.as_view.<locals>.view``), so two endpoints declared in
    one module with the same rate and the same key callable land on one shared
    cache key. That is not hypothetical: password-reset *verify* and *confirm*
    are both 10/m on client_ip in this module and were sharing a single bucket,
    meaning a user who used up their verify attempts could not then confirm. The
    group strings are the RATE_LIMITS keys, so the limit and its namespace are
    named in one place.

    Stacking note: the outermost decorator increments first and raises on block,
    so an inner tier does not count a request the outer tier already refused.
    Order the tiers accordingly — cheapest/most-likely first.

    Finally, the returned view stashes ``limits`` on the **request** before the
    rate decorators run. That is the only way the 429 renderer can report a real
    ``Retry-After``: django-ratelimit raises a bare ``Ratelimited()`` carrying no
    indication of which tier fired or when its window closes, so
    apps/core/views.ratelimited re-checks these tiers to find the wait. Stashed
    on the request rather than set as an attribute on the view function because
    ``resolve()`` returns the dispatcher for ``/inquiries/``, not the wrapped
    POST handler — the request object is the thing that always flows through.
    """
    for group, rate, key in reversed(limits):
        view = ratelimit(group=group, key=key, rate=rate, method="POST", block=True)(view)

    @wraps(view)
    def _tagged(request, *args, **kwargs):
        request.rate_limit_tiers = limits
        return view(request, *args, **kwargs)

    return _tagged


# Wrapped views are built here rather than inline in urlpatterns so a two-tier
# limit stays readable, matching apps/inquiries/urls.py.
# Tier ordering, applied to every endpoint below: burst first, then the
# narrowest axis, then the daily backstop last. Because the outermost decorator
# raises before an inner one counts, that ordering means a caller who trips the
# burst limit does NOT also spend their account allowance or their day — the
# coarse budgets are only consumed by requests that got past everything cheaper.
# Reversing it would let a few seconds of rapid retries eat a whole day.
#
# Every endpoint carries its own DAILY cap. The per-minute limits cap a burst and
# nothing else — 10/m sustained is 14,400 login attempts a day — and the daily
# ceiling used to come from the shared DRF `anon` throttle, one bucket per IP
# across all of these at once. That meant a morning of failed logins could leave
# someone behind the same NAT unable to finish a password reset, refused by
# traffic that was not theirs. Per-endpoint days remove that coupling; the shared
# ceiling is now only a safety net for endpoints nobody wired.
# Login is the ONE limited endpoint not wrapped here, and deliberately so. Its
# four tiers live in apps/accounts/login_guard.py and are applied inside
# MyTokenObtainPairView, because a decorator increments before the view knows
# whether the credentials were right — so correct logins were spending
# anti-brute-force budget and an office behind one NAT could lock itself out.
# Same groups, same rates, same bucket strings; only the moment of counting
# moved. See docs/adr/0002-login-failure-tracking.md.
_login = views.MyTokenObtainPairView.as_view()
_token_refresh = _rl(
    TokenRefreshView.as_view(),
    ("token_refresh", RATE_LIMITS["token_refresh"], client_ip),
    ("token_refresh_daily", RATE_LIMITS["token_refresh_daily"], client_ip),
)
_password_reset_request = _rl(
    PasswordResetRequestView.as_view(),
    ("password_reset_request", RATE_LIMITS["password_reset_request"], ip_and_email),
    ("password_reset_request_daily", RATE_LIMITS["password_reset_request_daily"], client_ip),
)
_password_reset_verify = _rl(
    PasswordResetVerifyView.as_view(),
    ("password_reset_verify", RATE_LIMITS["password_reset_verify"], client_ip),
    ("password_reset_verify_daily", RATE_LIMITS["password_reset_verify_daily"], client_ip),
)
_password_reset_confirm = _rl(
    PasswordResetConfirmView.as_view(),
    ("password_reset_confirm", RATE_LIMITS["password_reset_confirm"], client_ip),
    ("password_reset_confirm_daily", RATE_LIMITS["password_reset_confirm_daily"], client_ip),
)

urlpatterns = [
    # ── Health ──────────────────────────────────────────────────
    path('', views.Home, name='home'),  # GET
    path('secure/', views.secure, name='secure'),  # GET

    # ── Auth & tokens ───────────────────────────────────────────
    # Public POST endpoints are rate-limited (credential stuffing, code guessing,
    # email-bomb / enumeration). logout + force-password-change are authenticated,
    # so they're left unwrapped.
    path('auth/token/', _login, name='token_obtain_pair'),  # POST
    path('auth/token/refresh/', _token_refresh, name='token_refresh'),  # POST
    path('auth/token/logout/', views.LogoutView.as_view(), name='token_logout'),  # POST

    path('auth/password-reset/request/', _password_reset_request, name='password_reset_request'),  # POST
    path('auth/password-reset/verify/', _password_reset_verify, name='password_reset_verify'),  # POST
    path('auth/password-reset/confirm/', _password_reset_confirm, name='password_reset_confirm'),  # POST

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
