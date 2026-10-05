from rest_framework import serializers
from rest_framework.serializers import EmailField, ModelSerializer

from apps.core.permissions import is_staff_or_superuser
from apps.core.serializers import AttributionSerializerMixin
from apps.core.uploads import validate_image

from .models import Event, EventDay, EventImage

# AttributionSerializerMixin supplies created_by_display / last_updated_by_display
# and strips the raw actor FK ids — without it, `fields = '__all__'` below would
# serialize last_updated_by as a bare user pk (the old `last_updated_by: 1`).


class StaffOnlyFieldsMixin:
    """
    Fields listed in ``staff_only_fields`` are writable by staff/superusers only.
    For anyone else they become read-only: still in the response, silently
    dropped from the input — DRF's normal treatment of a read-only field.

    These are the public-portfolio fields. A client may edit their own event
    through the same serializer (update_event, the gallery endpoints), and
    without this they could publish it, or rewrite the copy on the public page,
    with a PATCH.

    Ignored rather than a 400 because the portal round-trips whole objects: a
    client PUT built from the GET response carries `is_published`, `headline`,
    etc. back unchanged, and refusing that would break ordinary saves. The
    write simply has no effect, exactly as for `id` or `created_at`.

    No request in context means no user, which counts as not staff — a caller
    that forgets the context gets the locked-down behaviour, not the open one.
    """

    staff_only_fields: tuple[str, ...] = ()
    # Also read-only for non-staff, but only once the event is published: they
    # show on the public portfolio page, so a client edit would go live with no
    # one reviewing it. Same silent-ignore semantics as staff_only_fields.
    published_locked_fields: tuple[str, ...] = ()

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not is_staff_or_superuser(user):
            locked = self.staff_only_fields
            if self.published_locked_fields and self._event_is_published():
                locked = (*locked, *self.published_locked_fields)
            for name in locked:
                if name in fields:
                    fields[name].read_only = True
        return fields

    def _event_is_published(self) -> bool:
        """
        Whether the event this write lands on is published. Only checked when
        the serializer was given input, because read_only only matters for
        input, and a read-only serializer (e.g. every cover_image in a list
        response) would otherwise cost a query per row to reach the event.
        """
        if not hasattr(self, "initial_data"):
            return False
        event = self._lock_event()
        return bool(event and event.is_published)

    def _lock_event(self):
        """The Event to test; the view's ``event`` context as a fallback."""
        return self.context.get("event")


class EventImageSerializer(StaffOnlyFieldsMixin, AttributionSerializerMixin, ModelSerializer):
    """
    One gallery image. `event` and `event_day` are read-only because the scope
    comes from the URL and the request body respectively, resolved and validated
    in the view — accepting them here would let a caller attach an image to an
    event they can't reach by putting someone else's id in the body.

    `is_published` (shown on the public portfolio or not) is staff-only.
    `alt_text` and `sort_order` are too once the event is published.
    """

    staff_only_fields = ("is_published",)
    published_locked_fields = ("alt_text", "sort_order")

    class Meta:
        model = EventImage
        fields = [
            "id", "event", "event_day", "image", "alt_text", "is_primary",
            "is_published", "sort_order", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "event", "event_day", "created_at", "updated_at"]

    def validate_image(self, value):
        return validate_image(value)

    def _lock_event(self):
        # Only an existing image is locked. A new upload on a published event
        # is allowed and starts unpublished (see views.event_gallery), so its
        # alt text is not on the public page.
        if isinstance(self.instance, EventImage):
            return self.instance.event
        return None


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


class EventSerializer(StaffOnlyFieldsMixin, _GalleryMixin, AttributionSerializerMixin, ModelSerializer):

    celebrant = EmailField(source='celebrant.email', read_only=True)

    # The public-portfolio fields. See StaffOnlyFieldsMixin.
    staff_only_fields = ("is_published", "headline", "description", "public_slug")

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
        

class EventDaySerializer(StaffOnlyFieldsMixin, _GalleryMixin, AttributionSerializerMixin, ModelSerializer):
    owner = serializers.CharField(source="owner.title", read_only=True)

    # The public page's per-day copy and URL segment. `content` is included even
    # though it predates the portfolio: it is now the narrative the public page
    # renders, so a client-editable `content` is a client-editable public page.
    # See StaffOnlyFieldsMixin.
    staff_only_fields = ("headline", "content", "slug")
    # The day's eyebrow on the public page, and the source of its slug.
    published_locked_fields = ("event_day_title",)
    venue_booking_status_display = serializers.CharField(
        source="get_venue_booking_status_display", read_only=True
    )

    class Meta:
        model = EventDay
        fields = '__all__'
        read_only_fields = ['id', 'owner', 'created_by', 'last_updated_by', 'created_at', 'updated_at']

    def validate_slug(self, value):
        """
        Unique within the event. The model's UniqueConstraint is conditional and
        `owner` is read-only here (it comes from the URL), so DRF generates no
        validator for it and a duplicate would surface as an IntegrityError 500.
        The owner is the instance's on update, the view-supplied `event` on
        create.
        """
        if not value:
            return None
        owner_id = self.instance.owner_id if self.instance else getattr(self.context.get("event"), "pk", None)
        if owner_id is not None:
            clash = EventDay.objects.filter(owner_id=owner_id, slug=value)
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError("Another day of this event already uses this slug.")
        return value

    def _gallery(self, obj: EventDay):
        """Every image attached to this day — the full gallery its page renders."""
        return list(obj.images.all())

    def _lock_event(self):
        if isinstance(self.instance, EventDay):
            return self.instance.owner
        return super()._lock_event()
        