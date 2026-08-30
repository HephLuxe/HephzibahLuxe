"""
apps/inquiries/services.py

Business logic for the public inquiry write path. The view stays thin:
validate → call create_inquiry() → respond with a fixed 201. Everything that
happens after a lead hits submit (dedupe, the row, the two emails) lives here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.exceptions import ValidationError

from apps.core.utils import stamp_attribution

from .models import InquiryForm

logger = logging.getLogger(__name__)

# How long an IDENTICAL submission is treated as a double-click rather than a
# second lead. Protects staff inboxes (and the lead's) from the duplicate row +
# duplicate pair of emails an impatient double-tap produces.
#
# Declared in config/settings.py alongside every rate-limit number, because it
# has to be read together with RATE_LIMITS["inquiry_submit_burst"]: this window
# must fit INSIDE the burst window, or a submission can be silently swallowed in
# a window the lead has no attempt left in. Kept as a module-level name so call
# sites and tests read the same way as before.
DEDUPE_WINDOW_SECONDS = settings.INQUIRY_DEDUPE_WINDOW_SECONDS


def _canonicalise(value):
    """
    Render one validated field into a stable, comparable string.

    Types that carry the same meaning in more than one shape are collapsed here,
    because two spellings of one value must not produce two fingerprints:

    * Decimal — ``5000`` and ``5000.00`` are the same budget. Quantised to the
      model's 2dp so a frontend that trims (or pads) trailing zeros between the
      click and the retry still dedupes.
    * date — ISO 8601, never repr().

    Everything else is str()'d. Text fields arrive already trimmed: DRF's
    CharField/EmailField default to trim_whitespace=True.
    """
    if isinstance(value, Decimal):
        # normalize() first so 5000.00 -> 5E+3, then quantize back to a fixed
        # 2dp scale: together these make every equal Decimal one string.
        return str(value.normalize().quantize(Decimal("0.01")))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _dedupe_key(payload: dict) -> str:
    """
    Namespaced + hashed cache key for the dedupe window: a fingerprint of the
    WHOLE submission, not of the email.

    Keyed on every field so the window only ever swallows a submission that is
    byte-for-byte the one already accepted. An email-only key made this an email
    lockout instead: a lead who resubmitted 40 seconds later with a corrected
    date got a 201 and no row — the correction was destroyed silently, which is
    the one failure here that cannot be recovered. A duplicate row costs staff
    an email they can see and delete; a lost lead is gone. So this errs strict.

    Nothing is given up on the case the window exists for: a real double-click
    resubmits identical form state, which still lands on the same fingerprint.

    Two normalisations before hashing:

    * ``sort_keys`` — the fingerprint must not depend on serializer field
      declaration order.
    * ``None`` values are dropped, so an omitted ``budget`` and an explicit
      ``"budget": null`` are one submission. That is the only non-required field,
      and the two spellings mean the same thing to the lead.

    ``payload`` is the dict create_inquiry() is about to persist (recaptcha_token
    is already popped in the view), so the fingerprint always covers exactly the
    stored row — a field added to the serializer is picked up with no edit here.

    Hashed so arbitrary submitted text can never produce an oversized or
    memcached-illegal key; namespaced because this cache is shared with
    django-ratelimit's counters.
    """
    canonical = {
        key: _canonicalise(value)
        for key, value in payload.items()
        if value is not None
    }
    # email is an identity, not free text: case/whitespace differences in it are
    # not a different lead. The remaining text fields stay case-sensitive — a
    # difference there means someone retyped, which under a strict key is a new
    # submission, and strict errs toward saving.
    if "email" in canonical:
        canonical["email"] = canonical["email"].lower().strip()

    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(blob.encode()).hexdigest()
    return f"inquiry_dedupe:{digest}"


def create_inquiry(validated_data: dict) -> InquiryForm | None:
    """
    Persist an inquiry and queue both emails (client acknowledgement + one
    internal alert per flagged staff member).

    Returns None when an IDENTICAL submission already landed inside the dedupe
    window — no row, no email. The caller still returns the same 201, so a
    double-click is indistinguishable from a single submit to the browser; with a
    whole-payload fingerprint that 201 is honest, because the submission being
    dropped provably is the one already accepted.
    """
    dedupe_key = _dedupe_key(validated_data)

    # cache.add() is the atomic set-if-absent primitive: it returns False when
    # the key already exists. A get()/set() pair would race against exactly the
    # double-click this defends against. It stays BEFORE the create — that
    # ordering is what makes a genuine concurrent double-click atomic — but the
    # claim is released again on failure (see the except below), otherwise a
    # submit that 500s would hold the key for the full window and every retry
    # inside it would be silently swallowed as a duplicate, losing the lead.
    if not cache.add(dedupe_key, True, DEDUPE_WINDOW_SECONDS):
        # The ONLY trace a swallowed submit leaves. Emitted so the real
        # double-click rate is measurable rather than guessed — which is what
        # docs/INQUIRY_V2_BACKLOG.md §7 (R5) asks for before the rate-limit
        # interaction is retuned. Only the key prefix is logged: the full digest
        # is derived from the lead's own details.
        logger.info(
            "Duplicate inquiry submission collapsed inside the dedupe window.",
            extra={
                "event": "inquiry_dedupe_hit",
                "dedupe_key_prefix": dedupe_key[:24],
                "window_seconds": DEDUPE_WINDOW_SECONDS,
            },
        )
        return None

    try:
        inquiry = InquiryForm.objects.create(**validated_data)

        # Imported here, not at module scope: notifications imports back into
        # the apps it serves, so a top-level import risks a circular import at
        # boot.
        from apps.notifications.services import queue_notification

        # Client acknowledgement — ONE param by design. The body is static copy
        # and the CTA is hardcoded in the Brevo template, so this deliberately
        # echoes back no event details, no dates, no budget, not even the email
        # address.
        queue_notification(
            recipient_email=inquiry.email,
            template_name="inquiry_received",
            context={"first_name": inquiry.first_name},
        )

        User = get_user_model()
        # is_staff is defensive: User.save() keeps it in sync with role, so a
        # client account whose flag got ticked by accident can never be
        # notified, and an offboarded staff member (is_active=False) drops off
        # without a flag edit.
        recipients = User.objects.filter(
            receives_inquiry_alerts=True, is_active=True, is_staff=True
        )

        if not recipients.exists():
            # The lead is already saved and acknowledged — this is not a failure
            # of the submission, but nobody is being told about it, which needs
            # to be visible. Emit the signal; Grafana decides whether to shout.
            logger.error(
                "An inquiry was submitted but no staff member is flagged to receive alerts.",
                extra={"event": "inquiry_no_recipients", "inquiry_id": str(inquiry.id)},
            )
            return inquiry

        # One queue_notification() call per staff member: _send_via_brevo takes
        # a single to_email, so status/retry/audit are per-address. Collapsing
        # this into one multi-recipient send would re-send to everyone on a
        # partial failure.
        for staff in recipients:
            queue_notification(
                recipient_email=staff.email,
                # The staff member this alert is for. A durable FK, so the row
                # still resolves to them in their notification feed after an
                # email change — the recipient_email__iexact fallback would not.
                # No engagement: a lead has none until it converts.
                recipient_user=staff,
                template_name="inquiry_submitted_internal",
                context={
                    "recipient_name": staff.first_name,
                    "first_name": inquiry.first_name,
                    "last_name": inquiry.last_name,
                    "email": inquiry.email,
                    "phone_number": inquiry.phone_number,
                    "contact_mode": inquiry.contact_mode,
                    "event_type": inquiry.event_type,
                    "desired_location": inquiry.desired_location,
                    "preferred_start_date": inquiry.preferred_start_date,
                    "preferred_end_date": inquiry.preferred_end_date,
                    # Decimal → str via _serialise_context; the template adds
                    # the ₦ and separators itself, exactly as milestone_paid
                    # does. The literal string keeps the template free of a
                    # conditional.
                    "budget": inquiry.budget if inquiry.budget is not None else "Not specified",
                    "details": inquiry.details,
                    "submitted_at": inquiry.created_at,
                    "inquiry_id": inquiry.id,
                },
            )

    except Exception:
        # The dedupe key was claimed before the row existed, so a failure here
        # (DB error, notification queueing blowing up) would otherwise leave a
        # 120s claim behind with nothing saved. Release it so the caller's retry
        # is treated as a fresh submission rather than a duplicate, then re-raise
        # for the exception handler to turn into the 500 the caller must see.
        cache.delete(dedupe_key)
        raise

    return inquiry


# ── Triage: status transitions ───────────────────────────────────────────────
# Which statuses may follow which. Same shape as
# apps/meetings/services.VALID_TRANSITIONS, deliberately — the backlog asked for
# "guarded the way meetings does it", and one state-machine idiom across the
# project beats two.
#
# The guard's job is to reject nonsense (`converted -> new`), NOT to enforce a
# script. Skip-ahead edges stay open on purpose: a lead who phones and is
# obviously qualified goes `new -> qualified` without an invented stop at
# `contacted`, and over-tightening here just produces 400s on legitimate triage.
#
# The four calls worth knowing:
#   * CONVERTED is near-terminal. Conversion (backlog §1) creates a user account
#     and an event; reversing it would orphan both, so the only exit is ARCHIVED.
#   * LOST is revivable. "They came back six months later" is a real workflow —
#     make it terminal and staff create a duplicate lead instead, which is worse.
#   * ARCHIVED keeps one exit, or a mis-click is permanent.
#   * A status may not list ITSELF. Same-status is handled as an idempotent
#     no-op in transition_inquiry_status below rather than as a table entry, so
#     the table stays a statement about real movement.
VALID_TRANSITIONS: dict[str, list[str]] = {
    InquiryForm.Status.NEW: [
        InquiryForm.Status.CONTACTED,
        InquiryForm.Status.QUALIFIED,
        InquiryForm.Status.LOST,
        InquiryForm.Status.ARCHIVED,
    ],
    InquiryForm.Status.CONTACTED: [
        InquiryForm.Status.QUALIFIED,
        InquiryForm.Status.CONVERTED,
        InquiryForm.Status.LOST,
        InquiryForm.Status.ARCHIVED,
    ],
    InquiryForm.Status.QUALIFIED: [
        InquiryForm.Status.CONVERTED,
        InquiryForm.Status.CONTACTED,
        InquiryForm.Status.LOST,
        InquiryForm.Status.ARCHIVED,
    ],
    InquiryForm.Status.CONVERTED: [InquiryForm.Status.ARCHIVED],
    InquiryForm.Status.LOST: [
        InquiryForm.Status.CONTACTED,
        InquiryForm.Status.ARCHIVED,
    ],
    InquiryForm.Status.ARCHIVED: [InquiryForm.Status.NEW],
}


def transition_inquiry_status(inquiry: InquiryForm, new_status: str, user=None) -> InquiryForm:
    """
    Move a lead to `new_status`, stamping who did it.

    Raises ``ValidationError`` when the move is not in VALID_TRANSITIONS — the
    view turns that into the INVALID_TRANSITION code, which is a different
    failure from "that is not a status at all" (VALIDATION_ERROR) and is checked
    by the caller first.

    **Re-setting the current status is an allowed no-op**, deliberately
    diverging from meetings, where a status never lists itself. A frontend
    double-click or an idempotent retry should not produce a 400 for a request
    that asks for the state the row is already in. It still writes, so
    `last_updated_by` / `updated_at` refresh — "someone looked at this and
    confirmed it" is worth recording, and a silent return would make the
    response's attribution stale relative to the action that produced it.

    `user` is stamped via the shared helper, which no-ops for an anonymous or
    missing actor — so `last_updated_by` keeps its previous value rather than
    being nulled by a caller that forgot to pass one.
    """
    if new_status != inquiry.status:
        allowed = VALID_TRANSITIONS.get(inquiry.status, [])
        if new_status not in allowed:
            raise ValidationError(
                f"Cannot move a lead from '{inquiry.status}' to '{new_status}'."
            )

    inquiry.status = new_status
    stamp_attribution(inquiry, user)
    # update_fields: a bare save() writes back every column the instance holds,
    # which on this model would mean re-writing the lead's own submitted words.
    # Narrow it to what triage owns.
    inquiry.save(update_fields=["status", "last_updated_by", "updated_at"])
    return inquiry
