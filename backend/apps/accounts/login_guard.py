"""
apps/accounts/login_guard.py

The login rate limits, moved off the URL decorator and into the view so they can
count **failed** attempts only. See docs/adr/0002-login-failure-tracking.md.

Why this module exists
----------------------
Every other limited endpoint in the project wraps its view with
``@ratelimit(...)`` at the URL. Login cannot, and the reason is structural: the
decorator increments *before* the view runs, so it has no idea whether the
credentials were right. A staff member logging in correctly was spending
anti-brute-force budget, and behind NAT one IP is not one person — a dozen staff
arriving together used to exhaust a per-IP burst between them while doing nothing
wrong.

django-ratelimit supports the split directly: ``get_usage(increment=False)``
asks "is this bucket full?" without counting, and a second call with
``increment=True`` counts. So the view checks on the way in and counts only on
the way out, and only when authentication actually failed.

What did NOT change
-------------------
The groups, the rates and the bucket strings are identical to what the decorator
produced, so ``RateLimitGroupIsolationTests`` still covers these tiers and a
counter in flight across a deploy is not orphaned. ``method="POST"`` is passed
explicitly for the same reason — it is part of the cache key.

The account tiers key on ``core.ratelimit.email_key_for(email)``, which takes
the already-parsed address. Reading ``request.body`` from in here would raise
``RawPostDataException`` — DRF has consumed the stream by the time the view
runs, which is why the URL-level variant could not simply be reused.
"""

from __future__ import annotations

import hashlib
import logging

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django_ratelimit.core import (
    _get_window,
    _make_cache_key,
    _split_rate,
    get_usage,
)

from apps.core.ratelimit import (
    RATE_LIMITS,
    client_ip,
    email_bucket_value,
    email_key_for,
)

logger = logging.getLogger(__name__)


def login_tiers(email: str):
    """The four buckets a login attempt is measured against.

    Order is the same as the old decorator stack — burst, then the narrowest
    axis, then the daily backstops — but the order no longer changes behaviour
    the way decorator nesting did. Every tier is checked before the attempt and
    every tier is counted after a failure, so none of them can shield another
    from being spent.

    Two axes, as before. The IP pair stops one machine grinding; the account pair
    caps attempts against one email no matter how many addresses they come from,
    which is the axis an IP-only limit leaves wide open to a distributed run.
    """
    by_email = email_key_for(email)
    return (
        ("auth_login", RATE_LIMITS["auth_login"], client_ip),
        ("auth_login_account", RATE_LIMITS["auth_login_account"], by_email),
        ("auth_login_account_daily", RATE_LIMITS["auth_login_account_daily"], by_email),
        ("auth_login_daily", RATE_LIMITS["auth_login_daily"], client_ip),
    )


def admin_login_tiers(email: str):
    """The buckets a **Django admin** sign-in is measured against.

    Two IP tiers of its own, so operator traffic and public API traffic never
    draw down each other's buckets — but the SAME account tiers as
    ``login_tiers``, because one account under attack is one account whichever
    door is being tried. The 5-strike lock on ``User.failed_login_count`` is
    shared for the same reason, and is applied by the guarded view.
    """
    by_email = email_key_for(email)
    return (
        ("admin_login", RATE_LIMITS["admin_login"], client_ip),
        ("auth_login_account", RATE_LIMITS["auth_login_account"], by_email),
        ("auth_login_account_daily", RATE_LIMITS["auth_login_account_daily"], by_email),
        ("admin_login_daily", RATE_LIMITS["admin_login_daily"], client_ip),
    )


def _usage(request, group, rate, key, *, increment):
    return get_usage(
        request, group=group, key=key, rate=rate, method="POST", increment=increment,
    )


def first_full_tier(request, tiers) -> dict | None:
    """The first exhausted tier as ``{"group", "retry_after"}``, else None.

    Comparison is ``count >= limit``, NOT django-ratelimit's own
    ``should_limit``. That flag is ``count > limit``, which is correct for the
    decorator because it has already incremented — the request being judged is
    included in the count. Here nothing has been counted yet, so ``>`` would
    admit one attempt beyond the ceiling on every tier.

    ``retry_after`` is carried out because the caller is the only place that
    knows it: the 429 is rendered by middleware from the exception alone, and a
    bare ``Ratelimited()`` cannot say when the window closes. See
    apps/core/views.ratelimited.

    ``get_usage`` returns None when rate limiting is disabled (the test runner)
    or the method does not match, and None means "no opinion" rather than "full".
    """
    for group, rate, key in tiers:
        usage = _usage(request, group, rate, key, increment=False)
        if usage is not None and usage["count"] >= usage["limit"]:
            return {"group": group, "retry_after": usage["time_left"]}
    return None


def record_failed_attempt(request, tiers) -> None:
    """Count this failed attempt against every tier.

    Called only after authentication has actually failed. A malformed request
    (no password, bad JSON) is a 400 rather than a credential guess and is not
    counted here — it is still covered by the project-wide ``anon`` ceiling.
    """
    for group, rate, key in tiers:
        _usage(request, group, rate, key, increment=True)


# ── The email-keyed failure counter ──────────────────────────────────────────
# A SECOND counter, beside User.failed_login_count, and it exists for exactly one
# reason: to close a user-enumeration oracle.
#
# The account counter can only exist for an address that HAS an account. If the
# "you are out of attempts, go and reset" response were driven by it alone, that
# response would itself answer "does this address have an account here?" — five
# wrong passwords and you know. This counter is keyed on the submitted email
# whether or not anything is behind it, so a real address and an invented one
# become indistinguishable at the same threshold.
#
# It lives in the cache rather than the database on purpose: a row per address
# anyone ever typed at the login form is a write amplifier and a junk table, and
# the data has no value once the window passes.
#
# The two counters are not redundant. The account one is durable, visible in the
# admin when triaging "the client says they cannot sign in", and survives a cache
# eviction; this one covers the addresses the other cannot represent. The view
# treats EITHER being at the ceiling as locked, so an eviction can only ever lose
# the weaker of the two.
_FAILURE_PREFIX = "login_failures:"


def _failure_key(email: str) -> str:
    # Hashed, not the raw address. Cache keys surface in Redis tooling, slow-log
    # output and monitoring dashboards, and an inventory of every email ever
    # typed at the login form is not something to leave lying there.
    return _FAILURE_PREFIX + hashlib.sha256(email.encode()).hexdigest()


def _ceiling() -> int:
    # Read lazily from the model so there is ONE declared threshold; importing
    # the user model at module scope here would be a circular import.
    return get_user_model().MAX_FAILED_LOGINS


def _window_seconds() -> int:
    return int(get_user_model().FAILED_LOGIN_WINDOW.total_seconds())


def email_failure_count(email: str) -> int:
    if not email:
        return 0
    return cache.get(_failure_key(email)) or 0


def record_email_failure(email: str) -> None:
    """Count one failed attempt against the submitted address.

    ``add`` then ``incr``, so the TTL is set once by the first failure and the
    window runs from there rather than being pushed forward by every subsequent
    attempt — otherwise a steady trickle of guesses could hold an address locked
    indefinitely. (A slightly different ageing rule from
    ``User.record_failed_login``, which restarts a stale run from the last
    failure. Both expire, which is the part that matters.)
    """
    if not email:
        return
    key = _failure_key(email)
    if not cache.add(key, 1, _window_seconds()):
        try:
            cache.incr(key)
        except ValueError:  # expired between the add and the incr
            cache.set(key, 1, _window_seconds())


def clear_email_failures(email: str) -> None:
    if email:
        cache.delete(_failure_key(email))


def email_is_locked(email: str) -> bool:
    return bool(email) and email_failure_count(email) >= _ceiling()


# ── Releasing an address (the admin's "unlock" button) ───────────────────────

def account_limit_state(email: str) -> dict:
    """Everything currently holding this address back, for display.

    Read-only and side-effect free, so it is safe to call from a changelist
    column that renders once per row.
    """
    value = email_bucket_value(email)
    tiers = []
    for group, rate, key in login_tiers(value):
        if key is client_ip:
            continue  # IP-keyed: not a property of this account
        limit, _ = _split_rate(rate)
        tiers.append({
            "group": group,
            "rate": rate,
            "count": _bucket_count(group, rate, value),
            "limit": limit,
        })
    return {"failures": email_failure_count(value), "tiers": tiers}


def _bucket_cache_key(group: str, rate: str, value: str) -> str:
    """Rebuild the key django-ratelimit would use for this bucket, right now.

    Uses the library's own helpers rather than a local copy of the format. The
    key embeds the CURRENT window, so this is only valid for the window it is
    called in — which is exactly right for "clear it now" and worthless to cache.
    """
    _, period = _split_rate(rate)
    return _make_cache_key(group, _get_window(value, period), rate, value, "POST")


def _bucket_count(group: str, rate: str, value: str) -> int:
    return cache.get(_bucket_cache_key(group, rate, value)) or 0


def release_account(email: str) -> int:
    """Clear every EMAIL-keyed thing blocking this address. Returns how many
    buckets were actually holding a count.

    Covers the failure counter and both account-keyed rate tiers, because a
    release that only cleared one of them would appear to work and then refuse
    the very next attempt — the operator would have no way to tell which.

    Deliberately does NOT touch the IP-keyed tiers: those are a property of a
    machine, not of an account, and clearing them from a user row would silently
    unblock whoever else is behind that address.
    """
    value = email_bucket_value(email)
    if not value:
        return 0

    released = 1 if email_failure_count(value) else 0
    clear_email_failures(value)

    for group, rate, key in login_tiers(value):
        if key is client_ip:
            continue
        if cache.delete(_bucket_cache_key(group, rate, value)):
            released += 1
    return released
