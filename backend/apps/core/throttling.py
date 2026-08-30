"""
apps/core/throttling.py

DRF's global throttles, corrected.

Two problems with using DRF's stock classes here, both verified — see
docs/RATE_LIMITING_AUDIT.md (D2–D5, P3):

1. ``BaseThrottle.get_ident`` is not the same "client" the rest of the project
   means. With ``NUM_PROXIES`` unset it uses the **entire** ``X-Forwarded-For``
   header string as the bucket identity, which means (a) prepending one address
   yields an unlimited supply of fresh buckets, (b) the proxy's own address is
   part of the key, so every bucket silently resets whenever the platform edge
   rotates, and (c) if the edge ever stops sending XFF at all, every anonymous
   request in the world collapses into a single bucket. Both classes below take
   their identity from ``apps.core.ratelimit`` instead, so the throttle and the
   per-endpoint limiters can never disagree about who is being limited.

2. ``UserRateThrottle`` at a *daily* rate is the wrong instrument for the
   authenticated surface, and is replaced rather than retuned — see
   ``UserBurstRateThrottle`` below.

The rates come from ``DEFAULT_THROTTLE_RATES`` in config/settings.py, which
reads them from env vars like every other limit in the project.
"""

import logging

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from apps.core.ratelimit import bucket_ip

logger = logging.getLogger(__name__)


class NearLimitLoggingMixin:
    """
    Emit ``event="throttle_near_limit"`` before a bucket actually fills.

    A block is a *lagging* signal — it only appears once somebody has already
    been refused, which is too late to tell you a ceiling is badly chosen. This
    is the leading one, and it is nearly free: ``SimpleRateThrottle`` already
    holds the request history in memory at the moment it decides, so reporting
    occupancy costs no extra cache round-trip.

    Only fires for the few callers approaching a ceiling, so it stays quiet in
    normal operation. Threshold is ``settings.THROTTLE_NEAR_LIMIT_FRACTION``,
    read at call time so a deploy can move it without a code change.
    """

    def throttle_success(self):
        # self.history excludes the current request until super() records it.
        used = len(self.history) + 1
        threshold = self.num_requests * getattr(
            settings, "THROTTLE_NEAR_LIMIT_FRACTION", 0.8
        )
        if used >= threshold:
            logger.warning(
                "Throttle bucket nearly full.",
                extra={
                    "event": "throttle_near_limit",
                    "scope": self.scope,
                    "used": used,
                    "limit": self.num_requests,
                    "window_seconds": self.duration,
                },
            )
        return super().throttle_success()


class ClientIPAnonRateThrottle(NearLimitLoggingMixin, AnonRateThrottle):
    """
    The shared ceiling on *anonymous* traffic, keyed on the real client IP prefix.

    Scope ``anon``. Inherited behaviour that matters: ``get_cache_key`` returns
    ``None`` for an authenticated request, so this class only ever applies to
    callers with no account. It is a per-IP ceiling shared across the
    unauthenticated endpoints (login, token refresh, the password-reset steps) —
    deliberately *not* the inquiry endpoint, which carries
    ``@throttle_classes([])`` because it has limits chosen specifically for it
    and must not draw down the same pool as failed logins.

    Known and accepted: one IP is not one person behind NAT/CGNAT, so an office
    or a mobile carrier gateway shares this ceiling. That is why the rate is
    env-tunable without a deploy.
    """

    def get_ident(self, request):
        return bucket_ip(request)


class UserBurstRateThrottle(NearLimitLoggingMixin, UserRateThrottle):
    """
    The only limit on the *accountable* surface: a short-window burst ceiling.

    Scope ``user_burst`` (a new scope, NOT the old ``user``). Keyed on the
    account — ``UserRateThrottle.get_cache_key`` uses ``request.user.pk`` — so
    this never resolves an IP and every account-holder is measured identically,
    whether they are a client or staff.

    **Why this replaced ``user: 500/day`` outright rather than raising it.** The
    problem with the old limit was the *window*, not the number. Every account in
    this project is created by staff (there is no public signup), so an
    account-holder is a known person who can simply be switched off with
    ``set_user_status``. Rate limiting is not the control for a misbehaving human
    and it is certainly not an exfiltration control — one request to a
    staff-visible list endpoint can return a lot of rows. The one thing a limit
    *is* good for here is catching a runaway client: a frontend stuck in a retry
    or polling loop. A sliding daily budget handles that case about as badly as
    possible — the loop burns the whole day's allowance in seconds and then locks
    a real person out for hours. A per-minute ceiling stops the same loop almost
    immediately and clears itself within the minute, and no human working the
    portal can plausibly reach it.

    The distinction the design cares about is therefore **accountable vs
    anonymous**, not client vs staff. Splitting the accountable side by role
    would put the tightest limit on exactly the people doing the most legitimate
    high-volume work (the lead inbox, the dashboard, the CSV export) while doing
    nothing an attacker would notice.
    """

    scope = "user_burst"

    def get_cache_key(self, request, view):
        """
        None — do not throttle — for an anonymous request.

        ``UserRateThrottle`` inherits a fallback that buckets an unauthenticated
        caller by ``get_ident(request)``, and that fallback quietly undid three of
        the defects this module exists to fix. Because these classes are project
        DEFAULTS, they run on the anonymous endpoints too, so every unauthenticated
        request was being given a ``user_burst`` bucket keyed on DRF's raw ident —
        the whole ``X-Forwarded-For`` string. Observed: one prepended address gave a
        fresh bucket, the proxy's own address sat inside the key, and with no XFF at
        all every anonymous caller in the world collapsed into
        ``throttle_user_burst_<proxy-ip>`` sharing one 120/m allowance.

        Returning None is the right fix rather than merely correcting the ident,
        because this scope is *defined* as the per-account ceiling. An anonymous
        request has no account to meter and is already covered by the ``anon``
        ceiling plus its endpoint's own limits; giving it a slot here was never
        the design, only an inherited default. ``get_ident`` is overridden below
        anyway, so if this skip is ever removed the identity is still the
        project's one answer rather than DRF's.
        """
        if not (request.user and request.user.is_authenticated):
            return None
        return super().get_cache_key(request, view)

    def get_ident(self, request):
        # Defence in depth: unreachable while get_cache_key skips anonymous
        # requests, and correct if that ever changes. It also means no throttle
        # in this project consults DRF's NUM_PROXIES setting on any path.
        return bucket_ip(request)
