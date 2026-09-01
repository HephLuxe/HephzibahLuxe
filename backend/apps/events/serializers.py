from rest_framework import serializers
from rest_framework.serializers import EmailField, ModelSerializer

from apps.core.serializers import AttributionSerializerMixin
from apps.core.uploads import validate_image

from .models import Event, EventDay, EventImage

# AttributionSerializerMixin supplies created_by_display / last_updated_by_display
# and strips the raw actor FK ids — without it, `fields = '__all__'` below would
# serialize last_updated_by as a bare user pk (the old `last_updated_by: 1`).

class EventImageSerializer(AttributionSerializerMixin, ModelSerializer):
    """
    One gallery image. `event` and `event_day` are read-only because the scope
    comes from the URL and the request body respectively, resolved and validated
    in the view — accepting them here would let a caller attach an image to an
    event they can't reach by putting someone else's id in the body.
    """

    class Meta:
        model = EventImage
        fields = [
            "id", "event", "event_day", "image", "alt_text", "is_primary",
            "sort_order", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "event", "event_day", "created_at", "updated_at"]

    def validate_image(self, value):
        return validate_image(value)


class _GalleryMixin(serializers.Serializer):
    """
    `images` (the gallery) and `cover_image` (the one flagged is_primary) for the
    two models that own galleries.

    `cover_image` is what replaced the retired `featured_image` / `event_images`
    fields, and it exists so a caller that only wants the cover — the portfolio
    index, an email — doesn't have to fetch the whole gallery and filter it.

    Both read from `obj.images.all()`, never `.filter()`, so a prefetch is
    actually used. That is the difference between one query and one per row on a
    list endpoint; the querysets in views.py prefetch accordingly.
    """

    images = serializers.SerializerMethodField()
    cover_image = serializers.SerializerMethodField()

    def get_images(self, obj):
        return EventImageSerializer(
            self._gallery(obj), many=True, context=self.context,
        ).data

    def get_cover_image(self, obj):
        cover = obj.cover_image
        if cover is None:
            return None
        return EventImageSerializer(cover, context=self.context).data


class EventSerializer(_GalleryMixin, AttributionSerializerMixin, ModelSerializer):

    celebrant = EmailField(source='celebrant.email', read_only=True)

    class Meta:
        model = Event
        fields = '__all__'
        read_only_fields = ['id','slug', 'created_by', 'last_updated_by', 'created_at', 'updated_at']

    def _gallery(self, obj: Event):
        """
        Event-level images only — a day's photographs belong to that day's
        gallery and would otherwise appear twice in one response, once here and
        once under the day.
        """
        return [img for img in obj.images.all() if img.event_day_id is None]

    def to_representation(self, instance: Event) -> dict:
        data = super().to_representation(instance)

        # Hide wedding fields unless it's a wedding
        if instance.event_type != "Wedding":
            data.pop("groom_name", None)
            data.pop("bride_name", None)

        # Hide birthday field unless it's a birthday
        if instance.event_type != "Birthday":
            data.pop("honoree_name", None)

        # Remove generic event_name for wedding & birthday
        if instance.event_type in ["Birthday", "Wedding"]:
            data.pop("event_name", None)

        return data
        

class EventDaySerializer(_GalleryMixin, AttributionSerializerMixin, ModelSerializer):
    owner = serializers.CharField(source="owner.title", read_only=True)
    venue_booking_status_display = serializers.CharField(
        source="get_venue_booking_status_display", read_only=True
    )

    class Meta:
        model = EventDay
        fields = '__all__'
        read_only_fields = ['id', 'owner', 'created_by', 'last_updated_by', 'created_at', 'updated_at']

    def _gallery(self, obj: EventDay):
        """Every image attached to this day — the full gallery its page renders."""
        return list(obj.images.all())
        