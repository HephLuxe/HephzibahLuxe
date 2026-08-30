"""
apps/inquiries/views.py

The one public write path in the project: an unauthenticated lead-capture POST.
Logic lives in services.py; this validates, verifies the captcha and responds.

Routes (under /api/v1/):
  POST   /inquiries/                     public — submit an inquiry
  GET    /inquiries/                     staff  — list leads
  GET    /inquiries/summary/             staff  — per-status pipeline counts
  GET    /inquiries/<uuid>/              staff  — one lead
  PATCH  /inquiries/<uuid>/status/       staff  — set the triage status

There is deliberately no public read, no update of submitted fields and no
delete: leads are business records, the only public verb is POST, and the status
sub-route exists precisely so a general PATCH cannot let staff quietly rewrite
what the client typed.
"""

from __future__ import annotations

from django.conf import settings
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.error_codes import INVALID_TRANSITION, VALIDATION_ERROR
from apps.core.pagination import InquiryPageNumberPagination
from apps.core.permissions import IsStaffOrSuperuser
from apps.core.ratelimit import resolve_client_ip

from . import dedupe, services
from .models import InquiryForm
from .recaptcha import ACTION_SUBMIT_INQUIRY, verify_recaptcha
from .serializers import InquiryCreateSerializer, InquirySerializer

# Sort keys a caller may pass to ?ordering=. Allow-listed rather than passed
# straight to order_by(): an unrestricted ordering lets a caller sort by any
# column and probe the table one page at a time.
ALLOWED_ORDERING = {"created_at", "preferred_start_date", "status", "event_type", "last_name"}

# ── Envelope helper ─────────────────────────────────────────────

def _error(detail: str, code: str, http_status: int, errors: dict | None = None) -> Response:
    body: dict = {"detail": detail, "code": code}
    if errors:
        body["errors"] = errors
    return Response(body, status=http_status)


@api_view(["POST"])
@permission_classes([])  # Public endpoint
@throttle_classes([])    # Opted OUT of the project-wide anon ceiling — see below
def submit_inquiry(request: Request) -> Response:
    """
    Capture a prospective client's inquiry: persist it, acknowledge it by email
    and alert the staff flagged to receive leads.

    Rate-limited at the URL, on two tiers (see urls.py). The 201 body is a fixed
    message and carries NO id and no echo of the saved row — this is
    unauthenticated, and reflecting stored data back would hand an attacker a
    confirmation oracle. A deduped double-submit gets the identical 201.

    **Why throttle_classes([]).** DRF's project-wide anon throttle keys on the
    client only — the view is not part of its cache key — so every
    unauthenticated endpoint draws from one shared per-IP pool. That put lead
    capture in the same bucket as failed logins and password-reset requests: a
    burst of either could leave a genuine lead unable to submit at all, and the
    lead would see a 429 caused entirely by traffic that had nothing to do with
    them. Since this endpoint has two limits chosen specifically for it, opting
    out of the shared pool leaves exactly the limits someone decided on. It is
    the ONLY endpoint in the project that does this; every other public POST
    keeps the shared ceiling as a backstop.
    """
    serializer = InquiryCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid inquiry.", VALIDATION_ERROR, 400, errors=serializer.errors)

    validated_data = dict(serializer.validated_data)
    # Not a model field — verified here, never persisted.
    recaptcha_token = validated_data.pop("recaptcha_token", "")

    # Skipped entirely while no secret is configured (local/CI/tests).
    #
    # `action` is not optional decoration: one reCAPTCHA site key covers every
    # form on the domain, so it is the only thing that stops a token harvested
    # from another page being replayed here (and this token being replayed
    # somewhere more valuable). The frontend must pass the SAME string to
    # grecaptcha.execute().
    if settings.RECAPTCHA_SECRET_KEY:
        if not recaptcha_token or not verify_recaptcha(
            recaptcha_token,
            action=ACTION_SUBMIT_INQUIRY,
            # Via the shared resolver, never REMOTE_ADDR directly — see
            # apps/core/ratelimit.py. Cannot raise here: the URL's rate-limit
            # decorators already called it before this view ran.
            remote_ip=resolve_client_ip(request),
        ):
            return _error("reCAPTCHA verification failed.", VALIDATION_ERROR, 400)

    services.create_inquiry(validated_data)

    # Record this submission as accepted, so an immediate identical repeat — a
    # double-click — is not counted against this lead's burst allowance. Read
    # back by dedupe.burst_rate on the NEXT request, before this view runs.
    #
    # Fingerprinted from request.data, NOT request.body: DRF has already consumed
    # the stream by now and Django raises RawPostDataException on a second read.
    # Both sides canonicalise the same parsed dict, so the digests match.
    #
    # Deliberately after create_inquiry and never in a finally: a submission that
    # failed was not accepted, and marking it would hand the retry a free pass
    # while the lead is still lost.
    dedupe.mark_submission_accepted(request.data)

    return Response(
        {"detail": "Your inquiry has been received. We'll be in touch within 2 business days."},
        status=status.HTTP_201_CREATED,
    )


###############################################     STAFF       ###############################################

def _filtered_inquiries(request: Request, *, include_status: bool):
    """The lead queryset with the caller's filters applied — shared by the list
    and the summary so the two can never disagree about what a filter means.

    `search` in particular is deliberately the same field set InquiryFormAdmin
    searches; duplicating that Q() block in the summary is exactly how it would
    drift the first time someone adds a column to one and not the other.

    `include_status` is False for the summary: filtering a PER-STATUS tally by
    status would return one populated row and five zeros, which answers nothing
    the caller didn't already know.
    """
    qs = InquiryForm.objects.all()

    if include_status:
        status_filter = (request.query_params.get("status") or "").strip()
        if status_filter:
            qs = qs.filter(status=status_filter)

    event_type = (request.query_params.get("event_type") or "").strip()
    if event_type:
        qs = qs.filter(event_type=event_type)

    search = (request.query_params.get("search") or "").strip()
    if search:
        qs = qs.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
            | Q(phone_number__icontains=search)
            | Q(desired_location__icontains=search)
        )

    return qs


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def inquiry_summary(request: Request) -> Response:
    """
    Staff only: how many leads sit in each triage status.

    GET /inquiries/summary/

    Response mirrors GET /event/<slug>/contacts/summary/ exactly — a flat list,
    one entry per choice, `{value, value_display, count}`:

        [{"status": "new", "status_display": "New", "count": 12}, ...]

    **Every status is present, including the empty ones.** A dashboard renders a
    fixed set of pipeline columns; omitting the zeros would make them appear and
    disappear as leads move. The aggregate below only returns statuses that
    actually occur, so the choice list is what drives the response and the counts
    are merged onto it.

    Honours ?event_type= and ?search= so the tallies match the list the same
    filters would produce. ?status= is ignored (see _filtered_inquiries), and
    ?ordering= / ?page= are meaningless here — no lead rows are returned.

    **One query, not one per status.** The three existing summary endpoints
    (contacts, meetings, conversations) each call .count() once per choice, which
    is six round trips here for six statuses. This is the whole point of the
    endpoint — a dashboard call that avoids work — and the response is
    byte-identical either way, so there is nothing to be gained by copying the
    N-query shape as well as the JSON shape.
    """
    qs = _filtered_inquiries(request, include_status=False)

    # .order_by() with no arguments is belt-and-braces, NOT load-bearing here.
    # The classic trap it guards is an ordering field leaking into the GROUP BY
    # and turning this into one row per lead — but Django stopped applying
    # Meta.ordering to aggregate queries in 3.1, and this queryset carries no
    # explicit ordering of its own (list_inquiries applies ?ordering= after
    # _filtered_inquiries returns, not inside it). Verified: the SQL is identical
    # with and without. Kept so the aggregate stays correct if a future caller
    # ever hands an already-ordered queryset to this block, which WOULD reinstate
    # the trap.
    counts = dict(
        qs.order_by()
        .values_list("status")
        .annotate(total=Count("id"))
        .values_list("status", "total")
    )

    return Response([
        {
            "status": value,
            "status_display": label,
            "count": counts.get(value, 0),
        }
        for value, label in InquiryForm.Status.choices
    ])


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def list_inquiries(request: Request) -> Response:
    """
    Staff only: the lead inbox behind the admin dashboard.

    Query params (all optional):
      status=<status>      filter by triage status
      event_type=<type>    filter by event type
      search=<text>        case-insensitive match on first / last name, email,
                           phone number and desired location — deliberately the
                           same field set InquiryFormAdmin searches, so the admin
                           and the API never disagree about what "search" means
      ordering=<field>     any key in ALLOWED_ORDERING, `-` for desc
                           (default: newest lead first)
      page / page_size     walk the pages / widen one (always paginated)

    A lead carries a name, an email, a phone number and a budget, so this is
    staff-gated for the same reason the user directory is (CLAUDE.md §9) — and
    always paginated, so no single request returns the whole table.
    """
    qs = _filtered_inquiries(request, include_status=True)

    ordering = (request.query_params.get("ordering") or "-created_at").strip()
    if ordering.lstrip("-") not in ALLOWED_ORDERING:
        return _error(
            f"Invalid ordering. Allowed: {', '.join(sorted(ALLOWED_ORDERING))} (prefix with '-' for descending).",
            VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST,
        )
    qs = qs.order_by(ordering)

    # ALWAYS paginated. This used to be opt-in (?page / ?page_size), which meant
    # the default response serialised every lead in the table in one go — so a
    # single request returned the whole business's lead list, names, emails,
    # phone numbers and budgets included. That is the response size a limit
    # cannot help with: rate limiting caps how MANY requests a caller makes, not
    # how much each one hands over, so a compromised staff token needed exactly
    # one request. Bounding the page is the control that actually applies.
    #
    # ?page_size= still works up to the paginator's max, and ?page= walks the
    # rest, so nothing is unreachable — it just takes as many requests as there
    # are pages, which is the point.
    #
    # The envelope gains `next`/`previous` beside the `count` and `results` the
    # unpaginated shape already returned, so a caller reading those two keys is
    # unaffected apart from receiving one page — and it is the same envelope the
    # rest of the portal returns. Page-number, not cursor:
    # StandardCursorPagination pins its own ordering, which would fight the
    # ?ordering= param above.
    #
    # Its own paginator class (10 per page) rather than the shared one: the
    # shared default is 7, pinned to the Budget Payment History Figma spec, and
    # the lead inbox should be re-sized without moving that table.
    paginator = InquiryPageNumberPagination()
    page = paginator.paginate_queryset(qs, request)
    return paginator.get_paginated_response(InquirySerializer(page, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def inquiry_detail(request: Request, inquiry_id) -> Response:
    """Staff only: one lead, every stored field. Read-only — see the module
    docstring for why there is no update-the-submission verb."""
    inquiry = get_object_or_404(InquiryForm, id=inquiry_id)
    return Response(InquirySerializer(inquiry).data)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def update_inquiry_status(request: Request, inquiry_id) -> Response:
    """
    Staff only: move a lead through triage.

    PATCH /inquiries/<uuid>/status/   { "status": "contacted" }

    The ONLY writable field on a submitted lead, which is why it has its own
    sub-route instead of a general PATCH.

    TWO different failures, two codes — the distinction is the point of having
    both:
      * "lost_forever" is not a status at all  -> VALIDATION_ERROR
      * "new" is a status, but not one a CONVERTED lead may move to
                                               -> INVALID_TRANSITION
    The value check runs first so a typo never reports itself as an illegal
    transition. Re-sending the status the lead already has is an accepted no-op
    (see services.transition_inquiry_status).

    The acting user is recorded on `last_updated_by`, which on this model means
    "who set the status" — status is the only mutable field. See InquiryForm's
    docstring for why there is no bespoke `status_updated_by` pair.
    """
    inquiry = get_object_or_404(InquiryForm, id=inquiry_id)

    new_status = request.data.get("status")
    if not new_status:
        return _error("status is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    if new_status not in InquiryForm.Status.values:
        return _error(
            f"Invalid status. Allowed: {', '.join(InquiryForm.Status.values)}.",
            VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST,
        )

    try:
        inquiry = services.transition_inquiry_status(inquiry, new_status, user=request.user)
    except ValidationError as e:
        return _error(str(e.detail[0]), INVALID_TRANSITION, status.HTTP_400_BAD_REQUEST)

    return Response(InquirySerializer(inquiry).data)
