"""
apps/inquiries/urls.py

Mounted under /api/v1/ in config/urls.py.
"""

from functools import wraps

from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from django_ratelimit.decorators import ratelimit

from apps.core.ratelimit import RATE_LIMITS, client_ip, ip_and_email

from . import dedupe, views


def _rl(view, *limits):
    """Wrap a plain FBV with one or more django-ratelimit decorators.

    Each entry in ``limits`` is ``(group, rate, key)``, applied so the FIRST
    listed ends up outermost. Placement outermost is mandatory: the Ratelimited
    exception must be raised before DRF's dispatch, which would convert it to a
    403, so that RatelimitMiddleware sees it and renders the 429 RATELIMIT_VIEW.
    block=True enforces; method='POST' only counts POSTs.

    ``group`` is explicit and required. Left to django-ratelimit it is derived
    from the view's module + qualname, and every ``as_view()`` result shares the
    qualname ``View.as_view.<locals>.view`` — so this endpoint's bucket would be
    separated from apps/accounts' identically-rated password-reset endpoint by
    nothing but the filename the view happens to live in. See the same note in
    apps/accounts/urls.py.

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


# TWO tiers, because one is not enough and the reason is specific to public lead
# capture: the email on this form is chosen by whoever is posting and costs them
# nothing, so a limit keyed on (IP, email) alone is bypassed by typing a
# different address. The strictness has to sit on the axis the submitter cannot
# vary, which is the IP.
_submit_inquiry = _rl(
    views.submit_inquiry,
    # Burst tier, outermost. What a real person hits: submitting again because
    # nothing visibly happened. Keyed on (IP, email) so it is that lead's own
    # allowance. Outermost so tripping it does NOT also draw down the IP tier
    # below — a fumbling lead shouldn't spend the budget meant for catching a
    # script. Its window is deliberately short (minutes, not an hour) so a
    # corrected resubmit isn't stranded until the next hour boundary, and it is
    # long enough to contain the ~120s dedupe window in services.py with room
    # for at least one correction after a double-click.
    #
    # Its rate is a CALLABLE, not a string: dedupe.burst_rate returns None for a
    # submission already accepted inside the dedupe window, and None makes
    # django-ratelimit skip the check without incrementing. That is what stops a
    # double-click costing two attempts — the request the dedupe window is about
    # to throw away no longer counts against the lead. See apps/inquiries/dedupe.py.
    ("inquiry_submit_burst", dedupe.burst_rate, ip_and_email),
    # Flood tier. What a script hits, and the only tier that caps a submitter who
    # varies the email on every request. Keyed on the IP alone. Generous for
    # genuine traffic — a real venue submits one inquiry, not ten an hour.
    #
    # ip_and_email parses request.body as JSON, which is why this endpoint is
    # JSON-only: a multipart/form-data post yields an empty email and collapses
    # the burst tier to IP-only bucketing. That degrades toward stricter, not
    # looser, and this tier covers the case regardless.
    ("inquiry_submit_ip", RATE_LIMITS["inquiry_submit_ip"], client_ip),
)


@csrf_exempt
def inquiries_collection(request, *args, **kwargs):
    """Dispatch /inquiries/ by method: GET = the staff lead list, anything else
    = the public submit.

    csrf_exempt is REQUIRED here and is NOT redundant with DRF. CsrfViewMiddleware
    reads the `csrf_exempt` attribute off the callback URL resolution returns —
    which is THIS dispatcher, not the DRF view it delegates to. Every other
    endpoint resolves straight to an `api_view`/`as_view()` result, which sets that
    attribute itself, so this is the only route the middleware still guards.
    Without the decorator a real browser or curl POST is rejected with
    `403 (CSRF cookie not set.)` before either branch runs. It does not surface in
    the suite: Django's test client skips CSRF unless built with
    enforce_csrf_checks=True — see InquiryCsrfTests, which pins this.

    Nothing is weakened by it. The project authenticates with JWT only (no
    SessionAuthentication in DEFAULT_AUTHENTICATION_CLASSES), so there is no
    cookie-borne ambient credential a cross-site POST could ride on — which is
    precisely why the same exemption is implicit on every other route.

    Two path() entries with the same pattern would NOT work — Django resolves on
    the path alone and always takes the first match, so the second entry would
    be dead and every GET would hit submit_inquiry's 405. The two handlers stay
    separate views because their permissions differ (public POST vs staff-only
    GET) and @permission_classes is evaluated per view.

    Only the POST branch carries the rate-limit wrappers, so the staff list is
    not URL-limited; a non-GET, non-POST verb falls through to submit_inquiry
    and gets DRF's 405. The staff GET is still covered by the per-account burst
    throttle every authenticated endpoint carries — it is the public POST that
    opts out of DRF throttling (see views.submit_inquiry)."""
    if request.method == "GET":
        return views.list_inquiries(request, *args, **kwargs)
    return _submit_inquiry(request, *args, **kwargs)


urlpatterns = [
    path("inquiries/", inquiries_collection, name="submit_inquiry"),  # POST | GET (staff list)
    # Listed before the <uuid:inquiry_id> route for clarity. The uuid converter
    # would never match "summary" anyway, so this is readability, not routing.
    path("inquiries/summary/", views.inquiry_summary, name="inquiry_summary"),  # GET — staff only
    path("inquiries/<uuid:inquiry_id>/", views.inquiry_detail, name="inquiry_detail"),  # GET
    path("inquiries/<uuid:inquiry_id>/status/", views.update_inquiry_status, name="update_inquiry_status"),  # PATCH
]
