"""
apps/events/public_serializers.py

The read shapes for the anonymous portfolio API. Separate module, separate
classes, and explicit field lists — none of which is decoration.

Why not reuse EventSerializer with fields popped
------------------------------------------------
EventSerializer and EventDaySerializer are declared ``fields = '__all__'``. On a
public endpoint that is a standing leak with a delay fuse: **the next field
anyone adds to either model is published the day it ships**, with no code change
and nothing to review. A denylist inverts the default in exactly the wrong
direction for the one endpoint with no authentication in front of it.

So these are allowlists. Adding a field to the model does nothing here; making
it public is a deliberate edit to this file.

What the portal serializers would have exposed, had they been reused:

    Event      celebrant  <- the client's EMAIL ADDRESS
               created_by_display / last_updated_by_display / created_at / ...
    EventDay   venue, venue_address  <- often a private family residence
               estimated_guest_count, venue_booking_status, dress_code,
               start_time, end_time
    EventImage id, event, event_day, attribution, timestamps

None of it appears on the public pages, and several items are things a client
would be entitled to be upset about. The allowlists below carry only what the
portfolio actually renders.
"""

from rest_framework import serializers

from .models import Event, EventDay, EventImage


class PublicEventImageSerializer(serializers.ModelSerializer):
    """
    One photograph. `image` is a plain permanent URL — gallery images live on the
    public R2 bucket behind a custom domain, unsigned, so there is nothing to
    mint and nothing to expire (see apps/core/storages.py). That is the whole
    reason this endpoint can be anonymous at all.

    No `id`: the public page has no use for it, and omitting it means these rows
    cannot be used to address anything through the authenticated API.
    """

    class Meta:
        model = EventImage
        fields = ["image", "alt_text", "sort_order"]


class PublicEventDaySerializer(serializers.ModelSerializer):
    """
    One day, as the portfolio renders it: eyebrow, headline, narrative, photos.

    Deliberately absent: `venue`, `venue_address`, `dress_code`,
    `estimated_guest_count`, `venue_booking_status`, `start_time`, `end_time`.
    They are planning data, and the venue address in particular is frequently a
    client's home.
    """

    images = serializers.SerializerMethodField()

    class Meta:
        model = EventDay
        fields = ["event_day_title", "headline", "content", "date", "images"]

    def get_images(self, obj: EventDay):
        # Filtered in Python over obj.images.all(), not with a .filter() query:
        # the queryset already prefetched the gallery, and a .filter() here would
        # discard that and cost one query per day — on the one endpoint anyone on
        # the internet can call as often as they like.
        return PublicEventImageSerializer(
            [i for i in obj.images.all() if i.is_published],
            many=True, context=self.context,
        ).data


class PublicEventListSerializer(serializers.ModelSerializer):
    """
    A portfolio index tile: one photo, the location/year eyebrow, the headline.

    `title` is excluded even though it looks harmless. It is derived from the
    celebrant's names (``services.generate_event_title`` -> "Sam & Pris's
    Wedding"), so publishing it publishes who the clients are. `headline` is the
    line written for public consumption, and it is the one the page shows.
    """

    cover_image = serializers.SerializerMethodField()
    year = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = ["slug", "headline", "event_type", "country", "state", "year", "cover_image"]

    def get_cover_image(self, obj: Event):
        """
        The primary image **among the published ones** — not ``obj.cover_image``.

        The model property answers the portal's question ("which image is this
        gallery's cover"), which is a different question from the public one
        ("which image may the website show"). Reusing it would put an unpublished
        photograph on the portfolio index whenever the cover is one staff chose
        to withhold — the single most visible way this flag could fail.

        Falls back to the first published image, so unticking the cover leaves a
        tile with a photo rather than a hole.
        """
        candidates = [
            i for i in obj.images.all()
            if i.event_day_id is None and i.is_published
        ]
        if not candidates:
            return None
        cover = next((i for i in candidates if i.is_primary), candidates[0])
        return PublicEventImageSerializer(cover, context=self.context).data

    def get_year(self, obj: Event):
        # Just the year: the eyebrow reads "LAGOS, NIGERIA — 2021", and the full
        # date is a fact about a private event that the page never displays.
        return obj.event_date.year if obj.event_date else None


class PublicEventDetailSerializer(PublicEventListSerializer):
    """The index tile plus the narrative and the days."""

    event_days = serializers.SerializerMethodField()

    class Meta(PublicEventListSerializer.Meta):
        fields = [*PublicEventListSerializer.Meta.fields, "description", "event_days"]

    def get_event_days(self, obj: Event):
        return PublicEventDaySerializer(
            obj.days.all(), many=True, context=self.context,
        ).data
