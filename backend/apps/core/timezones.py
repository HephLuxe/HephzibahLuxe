"""
apps/core/timezones.py

Per-recipient calendar dates, for a platform used worldwide.

The problem this solves
-----------------------
`TIME_ZONE = 'UTC'` and `USE_TZ = True` are correct and stay that way: every
`DateTimeField` is stored and compared in UTC, which is the only sane way to
store an instant.

But some of this platform's most important fields are **not** instants. A
`PaymentMilestone.due_date` and a `Meeting.date` are `DateField`s — a calendar
day, in the client's world. "Your final payment is due on 12 October" means the
12th where the client lives, not the 12th in UTC.

The daily digests used to compute `today = timezone.now().date()` and compare it
against those naive dates. That is UTC's today, and it is not the recipient's
today for anyone more than a few hours off UTC:

    digest runs 08:00 UTC on the 10th
      client in Lagos    (UTC+1)  -> local 09:00 on the 10th   same day, fine
      client in Auckland (UTC+13) -> local 21:00 on the 10th   same day, fine
      digest runs 23:00 UTC on the 10th   (or the row is near a boundary)
      client in Auckland (UTC+13) -> local 12:00 on the 11th   OFF BY ONE

The error is at most one day, which is why nothing has broken yet. But a
three-day payment lookahead that fires two or four days out — on an invoice a
luxury-events client is paying by bank transfer — is exactly the kind of thing
that gets noticed once and never trusted again.

The decision
------------
Resolve the calendar day **per recipient**, from a timezone stored on their
account, falling back to `settings.PLATFORM_DEFAULT_TIMEZONE`.

Considered and rejected: a single business timezone for the whole practice. That
is simpler and would be right for a practice serving one country, but it just
moves the off-by-one from "anyone not in UTC" to "anyone not in Lagos", and the
brief here is worldwide.

What this deliberately does NOT do
----------------------------------
It does not change **when** the digest fires. That is still one cron run at 08:00
UTC, so a client in Auckland reads it at 21:00 and one in Los Angeles at 01:00.
Fixing that means running the sweep hourly and sending to each timezone at its
own local 08:00 — a real improvement, a different change, and one that multiplies
the cron cadence. The correctness bug (which day counts as today) is fixed here;
the delivery-time preference is recorded in docs/adr/0001-remove-celery.md as a
follow-up.
"""

from __future__ import annotations

import logging
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Cache of validated ZoneInfo objects. ZoneInfo already caches internally, but
# this also caches the *rejection* of a bad name so a malformed value on one
# account doesn't re-log on every digest row.
_zone_cache: dict[str, ZoneInfo] = {}


def platform_default_timezone() -> ZoneInfo:
    """The fallback for an account with no timezone set."""
    return resolve_timezone_name(getattr(settings, "PLATFORM_DEFAULT_TIMEZONE", "UTC"))


def resolve_timezone_name(name: str | None) -> ZoneInfo:
    """
    Turn a timezone name into a ZoneInfo, degrading to UTC rather than raising.

    Fail-soft on purpose. This is called from inside the digest sweeps, and a
    single account with a typo'd or since-removed zone name must not take out the
    whole run — the alternative is one bad row silencing everyone's payment
    reminders. The bad value is logged with an `event=` label so it surfaces
    rather than passing silently.
    """
    if not name:
        return ZoneInfo("UTC")

    cached = _zone_cache.get(name)
    if cached is not None:
        return cached

    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.error(
            "unknown timezone %r — falling back to UTC",
            name,
            extra={"event": "unknown_timezone", "timezone": name},
        )
        zone = ZoneInfo("UTC")

    _zone_cache[name] = zone
    return zone


def user_timezone(user) -> ZoneInfo:
    """The recipient's timezone, or the platform default when they have none."""
    name = getattr(user, "timezone", "") if user is not None else ""
    if not name:
        return platform_default_timezone()
    return resolve_timezone_name(name)


def local_today(user) -> date:
    """
    Today's calendar date for this recipient.

    This is what a naive `DateField` (a payment due date, a meeting date) should
    be compared against — not `timezone.now().date()`, which is UTC's today.
    """
    return timezone.now().astimezone(user_timezone(user)).date()


def max_utc_offset_days() -> int:
    """
    How far any recipient's calendar date can differ from UTC's, in days.

    Always 1: real-world offsets span UTC-12 to UTC+14, so a local date is at most
    one day either side of the UTC date, never two.

    The digests use this to widen their database query into a superset that is
    guaranteed to contain every row *any* recipient could consider in range, then
    filter each row against its own recipient's `local_today`. One query, exact
    per-recipient answer — as opposed to a query per timezone.
    """
    return 1
