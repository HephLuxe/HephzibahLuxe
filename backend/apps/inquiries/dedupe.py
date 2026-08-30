"""
apps/inquiries/dedupe.py

Makes the rate limiter and the double-submit dedupe window agree about what a
double-click is.

The problem this solves. The two protections on ``POST /inquiries/`` count the
same event differently. The limiter must be applied **outermost at the URL** (or
DRF's dispatch converts ``Ratelimited`` into a 403 instead of the 429 envelope),
so it increments *before* the view runs. The dedupe window sits *inside*
``services.create_inquiry`` and correctly collapses a double-click into one lead
and one pair of emails. Net effect: a lead who double-clicked once spent two of
their attempts on a single inquiry, and the request that was thrown away counted
just as much as the one that was kept.

How it is fixed. django-ratelimit accepts a **callable** ``rate``, and returning
``None`` from it makes ``get_usage`` skip the check entirely — no increment, no
block (see ``django_ratelimit.core.get_usage``). So the burst tier asks this
module "have I already accepted this exact submission?" and, if so, declines to
count the request at all. That is the library's own supported mechanism; the
alternative considered was reaching into its private counter to decrement, which
is not a thing to build a lead-capture path on.

Two entry points, one fingerprint
---------------------------------
The fingerprint has to be computed at two different points in the request, where
different things are available — and it must come out identical at both:

``fingerprint_from_request_body(request)``
    Runs in the rate callable, OUTSIDE DRF, where the only thing available is the
    raw body. Parses it as JSON itself.

``fingerprint_from_payload(request.data)``
    Runs in the view, where DRF has already parsed the body. It must NOT re-read
    ``request.body``: by then the stream has been consumed and Django raises
    ``RawPostDataException``. That was not a theoretical concern — an earlier
    version of this module did exactly that and turned every submission into a
    500 whenever ``RATELIMIT_ENABLE`` was false, because the rate callable (which
    would otherwise have cached ``_body`` first) never ran.

For a JSON request the two see the same dict, so the same canonicalisation gives
the same digest. That equality is what the whole mechanism rests on, and
``InquiryDedupeFingerprintTests`` pins it.

Note this is a *second*, separate fingerprint from ``services._dedupe_key``,
which hashes the **validated** payload — Decimals quantised, dates as ``date``
objects, ``None``s dropped. That dict does not exist yet when the rate callable
runs. The two never need to agree with each other; each only has to be
self-consistent between where it is written and where it is read.

Failure mode, deliberately chosen. Every uncertainty here resolves to "count the
request", which is exactly the behaviour before this module existed. A body that
will not parse, a marker that expired, a first-of-two request still in flight —
all fall through to the normal rate. Nothing here can *loosen* a limit by being
wrong; it can only fail to tighten one.

What it does NOT skip: the per-IP flood tier. Only the burst tier — the one whose
job is "this lead's own allowance" — ignores a duplicate. The flood tier counts
every request that arrives, so replaying an identical payload forever is still
bounded, even though each replay writes no row and sends no email.
"""

from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.core.cache import cache

from apps.core.ratelimit import RATE_LIMITS

# Namespaced away from both django-ratelimit's counters and
# services._dedupe_key's claims, all three of which share the default cache.
_MARKER_PREFIX = "inquiry_seen_raw:"

# A single-use credential, not part of the submission's identity: a retry may
# legitimately carry a fresh one for the same lead, and it must still be
# recognised as the same lead.
_IGNORED_FIELDS = frozenset({"recaptcha_token"})


def _fingerprint(payload) -> str | None:
    """
    Hash one submission into a cache key, or None when there is nothing usable.

    Canonicalisation is minimal on purpose — sort the keys, drop nulls, no
    whitespace. It only has to make a *byte-identical resubmit* hash the same,
    which is precisely what a double-click produces. It does not need to
    recognise two different spellings of the same lead: a submission that differs
    at all should be counted, or the burst tier could be walked indefinitely by
    editing one character.

    ``default=str`` so a value the JSON encoder would reject can never raise on
    the public write path — it degrades to a stable string instead.
    """
    if not isinstance(payload, dict) or not payload:
        return None

    canonical = {
        key: value
        for key, value in payload.items()
        if value is not None and key not in _IGNORED_FIELDS
    }
    if not canonical:
        return None

    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return _MARKER_PREFIX + hashlib.sha256(blob.encode()).hexdigest()


def fingerprint_from_request_body(request) -> str | None:
    """
    Fingerprint from the RAW body — the rate-callable side.

    Safe to read ``request.body`` here: the limiter runs outermost, so nothing
    has touched the stream yet, and DRF re-parses the buffered content
    downstream. JSON only, which is why a ``multipart/form-data`` post simply
    falls through to being counted.
    """
    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, AttributeError):
        return None
    return _fingerprint(body)


def fingerprint_from_payload(payload) -> str | None:
    """
    Fingerprint from an already-parsed payload — the view side.

    Pass ``request.data``. See the module docstring for why this must not go back
    to ``request.body``.
    """
    return _fingerprint(payload)


def mark_submission_accepted(payload) -> None:
    """
    Record that this exact submission has been accepted, so an immediate repeat
    is not counted against the lead's burst allowance.

    Called from the view after a 201 — including the 201 a deduped repeat gets,
    which refreshes the window. Refreshing is safe: a caller replaying the same
    payload indefinitely still spends their per-IP flood allowance on every
    attempt, so the free pass is bounded even though it can be extended.

    Uses the same window as the dedupe itself. They are one decision — "this is
    the submission we already have" — and must not be able to drift apart.
    """
    fingerprint = fingerprint_from_payload(payload)
    if fingerprint is None:
        return
    cache.set(fingerprint, True, settings.INQUIRY_DEDUPE_WINDOW_SECONDS)


def burst_rate(group, request):
    """
    django-ratelimit ``rate`` callable for the inquiry burst tier.

    Returns the configured rate normally, or **None** for a submission already
    accepted inside the dedupe window — and None means django-ratelimit skips the
    check for this request entirely, so the duplicate is neither counted nor
    blocked.

    A read, never a write: the marker is claimed by the view after a successful
    response. Keeping this side-effect free is what makes it safe to run on every
    request, before the view has decided anything.
    """
    fingerprint = fingerprint_from_request_body(request)
    if fingerprint is not None and cache.get(fingerprint):
        return None
    return RATE_LIMITS["inquiry_submit_burst"]
