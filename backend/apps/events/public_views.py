"""
apps/events/public_views.py

The anonymous portfolio API — the only read endpoints on the platform with no
authentication in front of them.

Two safety properties, both enforced here rather than in the serializers:

1. **The queryset filters on ``is_published``.** An unpublished event is a 404,
   not a 200 with fields removed, so nothing about it is observable — not its
   existence, not its slug.
2. **The serializers are allowlists** (see public_serializers.py), so a field
   added to a model later is not published by default.

The filtering lives in the view and the shaping lives in the serializer on
purpose: those are the two independent things that would each have to fail for a
private event to leak, and keeping them apart means one mistake is not enough.
"""

import logging

from django.core.cache import cache
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.response import Response

from apps.core.error_codes import NOT_FOUND
from apps.core.throttling import PortfolioRateThrottle

from .models import Event
from .public_serializers import PublicEventDetailSerializer, PublicEventListSerializer

logger = logging.getLogger(__name__)

# Long enough to absorb the traffic a shared link produces, short enough that
# staff publishing an event see it appear without being told to wait. Both views
# also invalidate on save (see signals.py), so this is a ceiling on staleness
# rather than the mechanism for freshness.
PORTFOLIO_CACHE_SECONDS = 300

_LIST_CACHE_KEY = "portfolio:events"


def _detail_cache_key(slug: str) -> str:
    return f"portfolio:event:{slug}"


def _published():
    """
    Published events with the event-level gallery prefetched — all the index
    tile reads (``cover_image``). The detail view adds the days on top.

    Publicly addressed by ``public_slug`` only, so an event without one has no
    public URL; the list view drops (and logs) those.
    """
    return (
        Event.objects.filter(is_published=True)
        .prefetch_related("images")
        .order_by("-event_date")
    )


def _published_with_days():
    """
    ``_published()`` plus the days and their galleries, for the detail view.

    ``days__images`` matters as much as ``images``: without it the detail
    response costs one query per event day, on an endpoint any visitor can call
    as often as they like.
    """
    return _published().prefetch_related("days", "days__images")


@api_view(["GET"])
# No authentication at all, not just no permission check: with the project's
# JWTAuthentication left in place, a stale or invalid `Authorization: Bearer`
# header (a visitor whose portal session expired) is a 401 on a public page.
@authentication_classes([])
@permission_classes([])  # Public endpoint — see module docstring.
# Own per-IP bucket, not the shared `anon` one: ISR revalidation from shared
# Vercel IPs exceeded it. See apps/core/throttling.PortfolioRateThrottle.
@throttle_classes([PortfolioRateThrottle])
def portfolio_events(request):
    """
    GET — the portfolio index. Published events, newest first.

    Not paginated: the portfolio is a curated handful, and the page renders all
    of them as a grid with client-side category tabs. If it ever grows past a
    few dozen this needs the standard envelope, which is a breaking change for
    the frontend — so it is worth revisiting before that, not after.
    """
    cached = cache.get(_LIST_CACHE_KEY)
    if cached is not None:
        return Response(cached)

    # Partitioned in Python over the one query rather than filtered in SQL, so
    # the events being dropped can be named in the log without a second query.
    # Only reachable for a published event with no headline (save() generates
    # public_slug from the headline) — staff need to hear about it, the
    # visitor does not.
    events, unaddressable = [], []
    for event in _published():
        (events if event.public_slug else unaddressable).append(event)
    if unaddressable:
        logger.warning(
            "Published event(s) without a public_slug left off the portfolio",
            extra={"event_ids": [e.pk for e in unaddressable]},
        )

    data = PublicEventListSerializer(
        events, many=True, context={"request": request},
    ).data
    cache.set(_LIST_CACHE_KEY, data, PORTFOLIO_CACHE_SECONDS)
    return Response(data)


@api_view(["GET"])
# No authentication at all, not just no permission check: with the project's
# JWTAuthentication left in place, a stale or invalid `Authorization: Bearer`
# header (a visitor whose portal session expired) is a 401 on a public page.
@authentication_classes([])
@permission_classes([])  # Public endpoint — see module docstring.
# Own per-IP bucket, not the shared `anon` one: ISR revalidation from shared
# Vercel IPs exceeded it. See apps/core/throttling.PortfolioRateThrottle.
@throttle_classes([PortfolioRateThrottle])
def portfolio_event_detail(request, slug):
    """
    GET — one published event, its days and their galleries.

    An unpublished slug is a 404 rather than a 403: a 403 would confirm the
    event exists, which is a fact about a private client engagement.

    ``slug`` is matched against ``public_slug`` only. The internal
    ``Event.slug`` is a 404 here even for a published event.
    """
    key = _detail_cache_key(slug)
    cached = cache.get(key)
    if cached is not None:
        return Response(cached)

    try:
        event = _published_with_days().get(public_slug=slug)
    except Event.DoesNotExist:
        return Response(
            {"detail": "No such event.", "code": NOT_FOUND},
            status=status.HTTP_404_NOT_FOUND,
        )

    data = PublicEventDetailSerializer(event, context={"request": request}).data
    cache.set(key, data, PORTFOLIO_CACHE_SECONDS)
    return Response(data)


def invalidate_portfolio_cache(*slugs: str | None) -> None:
    """
    Drop the cached portfolio responses. Called from signals.py on any write to
    an Event, EventDay or EventImage, with the event's PUBLIC slug(s) — the
    current one, and the previous one when staff have just renamed it.

    The index is always dropped because a publish, an unpublish, a headline edit
    or a new cover all change it. A detail key is dropped for each slug the
    caller knows — every case except a delete that has already lost it, and
    there the 5-minute TTL closes the gap.
    """
    cache.delete(_LIST_CACHE_KEY)
    for slug in {s for s in slugs if s}:
        cache.delete(_detail_cache_key(slug))
