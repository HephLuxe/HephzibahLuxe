import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from apps.core.models import AttributedModel
from apps.core.storages import select_public_media_storage
from apps.core.utils import event_gallery_upload_path


class VenueBookingStatus(models.TextChoices):
    NOT_BOOKED = "not_booked", "Not Booked"
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"


# Create your models here.
class Event(AttributedModel):
    EVENT_TYPE = [
        ("Birthday", "Birthday Party"),
        ("Wedding", "Wedding"),
        ("Corporate", "Corporate Event"),
        ("Social Events", "Social Events"),
        ("Others", "Others"),
    ]
    celebrant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="events", null=True)
    title = models.CharField( max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    country = models.CharField( max_length=255)
    state = models.CharField( max_length=255)
    event_date = models.DateField(help_text="Date when the event takes place")
    event_venue = models.CharField(max_length=255, help_text="Venue where the event takes place", null=True)
    # There is no `featured_image` column. The cover is the EventImage in this
    # event's gallery flagged is_primary — see `cover_image` below. It used to be
    # a single ImageField, which meant staff had one shot at the cover and no way
    # to keep alternates; two fields both claiming to set the cover would have
    # been worse than either alone.
    event_type = models.CharField(max_length=255, choices=EVENT_TYPE, blank = True, null=True)
    # `title` is DERIVED (services.generate_event_title) and never client-supplied
    # — "Priscilla & Samuel's Wedding". The portal depends on that mechanical form,
    # so it stays. `headline` is the editorial line written by staff for the public
    # page: "A Golden 50th: An Intimate Two-Day Celebration of Family, Faith & Joy".
    # Two fields because they answer different questions: which event is this, and
    # how do we tell its story.
    headline = models.CharField(
        max_length=255, blank=True,
        help_text=(
            "Editorial headline for the public page, e.g. 'A Golden 50th: An Intimate "
            "Two-Day Celebration of Family, Faith & Joy'. Distinct from `title`, which "
            "is generated from the celebrant names and drives the portal."
        ),
    )
    description = models.TextField(
        blank=True, null=True,
        help_text="Long-form narrative for the public page — the paragraphs under `headline`.",
    )
    # OPT-IN, and the default matters more than the field: every event in this
    # table belongs to a real client, and most of them are jobs in progress
    # rather than portfolio pieces. Defaulting to False means an event becomes
    # public only when someone says so, and a bug in the public API's filtering
    # exposes nothing until that has happened. Publishing is event-level: it
    # takes the event's days and their galleries with it.
    is_published = models.BooleanField(
        default=False,
        help_text=(
            "Show this event on the public portfolio. Off by default — publishing "
            "also exposes every event day and gallery image beneath it."
        ),
    )
    
    groom_name = models.CharField(max_length=255, blank = True, null=True)
    bride_name = models.CharField(max_length=255, blank = True, null=True)
    honoree_name = models.CharField(max_length=255, blank = True, null=True)
    event_name = models.CharField(max_length=255, blank=True, null=True)

    # created_by / last_updated_by come from AttributedModel (apps/core/models.py)
    # — who created this event and who last edited it, shown as "Created by" /
    # "Last Updated by …".
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        # Slug is generated ONCE, on creation, and frozen after that — never
        # regenerated when the title later changes. It used to regenerate on
        # every title edit, which silently broke two things: any bookmarked/
        # shared portal URL keyed on the old slug, and every already-uploaded
        # file, since upload paths (core/utils.py) embed the event slug —
        # existing DB rows kept pointing at the old path while new uploads
        # went to the new one. See HEPHZIBAH_LUXE_AUDIT_AND_PLAN.md §4.3.
        if not self.pk or not self.slug:
            base_slug = slugify(self.title)
            slug = base_slug
            num = 1
            while Event.objects.filter(slug=slug).exists():
                slug = f'{base_slug}-{num}'
                num += 1
            self.slug = slug

        super().save(*args, **kwargs)

    def get_portal_id(self):
        try:
            return self.celebrant.portal.id
        except AttributeError:
            raise ValueError(
                f"Event '{self.title}' has no celebrant or the celebrant has no portal. "
                "Cannot generate upload path."
            )

    @property
    def cover_image(self):
        """
        The event-level image flagged is_primary — the single tile the public
        portfolio index renders for this event.

        Reads from `self.images.all()` rather than issuing `.filter(...)` so a
        caller that prefetched the gallery pays no extra query; a list page over
        N events would otherwise cost N. Falls back to the first image so an
        event whose primary was deleted still shows something instead of a hole.
        """
        event_level = [img for img in self.images.all() if img.event_day_id is None]
        if not event_level:
            return None
        return next((img for img in event_level if img.is_primary), event_level[0])

class EventDay(AttributedModel):
    # UUID, not the default BigAutoField — this PK is exposed directly in
    # URLs (/event/<slug>/event_day/<id>/); a sequential int PK there lets
    # anyone enumerate every event day by incrementing the number.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="days")
    # The public page renders three distinct pieces of text per day, so there are
    # three fields and not two:
    #
    #   event_day_title  ->  eyebrow    "PRE-BIRTHDAY PHOTOSHOOT" / "EVENT NO. 1"
    #   headline         ->  headline   "A Moment Before Fifty - A Pre-Birthday Portrait Experience"
    #   content          ->  narrative  the paragraphs beneath it
    #
    # `event_day_title` keeps its original short-label meaning because other apps
    # already treat it that way — the client notification text (views.py), the
    # contact-copy confirmation (contacts/views.py) and the admin label
    # (contacts/admin.py, "Day 1 - Traditional Wedding") all interpolate it inline,
    # where an 80-character editorial headline would read badly.
    event_day_title = models.CharField(
        max_length=255, null=True, blank=True,
        help_text=(
            "Short label / eyebrow above the headline. Either the kind of day "
            "('Traditional Wedding', 'Pre-Birthday Photoshoot') or its position in "
            "the sequence ('Event No. 1'). Typed as it should appear — the numbering "
            "is editorial, not computed, because not every day is part of the "
            "numbered sequence."
        ),
    )
    headline = models.CharField(
        max_length=255, blank=True,
        help_text=(
            "Editorial headline for this day, e.g. 'A Moment Before Fifty - A "
            "Pre-Birthday Portrait Experience'."
        ),
    )
    date = models.DateField()
    start_time = models.TimeField(blank=True, null=True)
    end_time = models.TimeField(blank=True, null=True)
    content = models.TextField(
        blank=True, null=True,
        help_text=(
            "Long-form narrative for this day — the paragraphs under `headline`. "
            "Short one-line summaries predate this field having a defined meaning "
            "and are still valid; they just render as a very short story."
        ),
    )
    # No `event_images` column either — despite the plural name it held exactly
    # one image. A day's photographs are EventImage rows pointing at it, and the
    # one flagged is_primary is the card cover (see `cover_image`).

    venue = models.CharField(max_length=255, blank=True)
    venue_address = models.TextField(blank=True)
    venue_booking_status = models.CharField(
        max_length=20, choices=VenueBookingStatus.choices, blank=True,
    )
    dress_code = models.CharField(max_length=255, blank=True)
    estimated_guest_count = models.PositiveIntegerField(null=True, blank=True)

    # created_by / last_updated_by come from AttributedModel (apps/core/models.py).
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    class Meta:
        ordering = ['date', 'start_time']
        verbose_name_plural = "Event Days"

    def __str__(self):
        return f"{self.owner.title} - {self.date}"

    def get_portal_id(self):
        try:
            return self.owner.celebrant.portal.id
        except AttributeError:
            raise ValueError(
                f"EventDay '{self.event_day_title}' owner has no celebrant or the celebrant has no portal. "
                "Cannot generate upload path."
            )

    @property
    def cover_image(self):
        """
        This day's image flagged is_primary — the cover on the day's card in the
        event overview. Same prefetch-friendly access and same fallback as
        Event.cover_image.
        """
        images = list(self.images.all())
        if not images:
            return None
        return next((img for img in images if img.is_primary), images[0])

    @property
    def day_number(self):
        """
        1-based position of this day among its event's days, ordered by date/start_time.

        Counts EVERY day, which is why it does not drive the public "EVENT NO. n"
        eyebrow — a pre-birthday photoshoot sorts first by date but sits outside
        the numbered sequence, so deriving the eyebrow from this would render the
        first celebration day as "No. 2". That label is `event_day_title`, typed
        by staff. This property is for internal ordering (see contacts/admin.py).
        """
        day_ids = list(self.owner.days.order_by("date", "start_time").values_list("id", flat=True))
        try:
            return day_ids.index(self.id) + 1
        except ValueError:
            return None

class EventImage(AttributedModel):
    """
    One photograph in a gallery. The same row type serves both levels, told apart
    by whether `event_day` is set:

      * `event_day` NULL  -> an event-level image. The public portfolio index
        shows only the one flagged is_primary; the rest are alternates staff can
        swap the cover to without re-uploading.
      * `event_day` set   -> one photograph in that day's gallery, all of which
        render on the day's page. The one flagged is_primary is the cover on the
        day's card in the event overview.

    One model rather than an EventImage/EventDayImage pair: the upload path needs
    the event either way (paths are keyed on `{event_id}-{slug}`), so a separate
    day model would carry a redundant FK up to the event or re-walk `owner` on
    every path build. One model also means one serializer, one set of endpoints
    and one blob-cleanup receiver instead of two near-identical copies. The cost
    is that "belongs to this event" and "belongs to a day of this event" have to
    agree, which `clean()` enforces below.

    `event` is never null even for a day-level image — it is the anchor the
    storage path and the permission check both read, and deriving it from
    `event_day.owner` would make both depend on a join that may not be loaded.
    """

    # UUID for the same reason as EventDay: this pk appears in URLs
    # (/event/<slug>/images/<id>/), and a sequential int there lets anyone
    # enumerate every image on the platform by counting upwards.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="images")
    event_day = models.ForeignKey(
        EventDay, on_delete=models.CASCADE, related_name="images", null=True, blank=True,
        help_text="Set for a day gallery image; leave empty for an event-level image.",
    )
    # max_length=500 for the same reason the retired single-image fields needed
    # it — the path is portal UUID + event id + slug + day id + image id +
    # the original filename, well past ImageField's 100-char default.
    image = models.ImageField(
        upload_to=event_gallery_upload_path, storage=select_public_media_storage, max_length=500,
    )
    alt_text = models.CharField(
        max_length=255, blank=True,
        help_text="Describes the photograph for screen readers and for when it fails to load.",
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="The cover for this gallery. At most one per event, and one per event day.",
    )
    # Per-image curation WITHIN a published event. Defaults to True, which is
    # the opposite of Event.is_published and deliberately so — the two flags do
    # different jobs:
    #
    #   Event.is_published  is the SECURITY boundary. Default False, because
    #                       every event belongs to a real client and must not
    #                       become public by accident.
    #   this flag           is an EDITORIAL filter inside a gallery that is
    #                       already public. Defaulting it False would mean
    #                       publishing an event shows a page with no
    #                       photographs until someone ticks forty checkboxes,
    #                       which trains staff to tick them all without looking.
    #
    # Nothing is public until the event is published, so a permissive default
    # here costs no exposure: it only decides which of an already-public
    # event's photographs appear.
    is_published = models.BooleanField(
        default=True,
        help_text=(
            "Show this photograph on the public portfolio. Has no effect until the "
            "event itself is published. Untick to keep a shot in the client's portal "
            "without putting it on the website."
        ),
    )
    sort_order = models.PositiveIntegerField(
        default=0, help_text="Ascending display order within the gallery; ties break on upload time.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # created_at breaks sort_order ties so the order is total, not merely
        # partial — a gallery of images all left at the default 0 would otherwise
        # come back in whatever order Postgres felt like, and could differ
        # between two requests for the same page.
        ordering = ["sort_order", "created_at"]
        verbose_name = "Event Image"
        verbose_name_plural = "Event Images"
        constraints = [
            # Partial uniques, one per scope. Without the event_day__isnull=True
            # arm on the first, a day-level primary would count against its
            # event's primary and the two galleries would fight over one slot.
            models.UniqueConstraint(
                fields=["event"],
                condition=models.Q(is_primary=True, event_day__isnull=True),
                name="unique_primary_image_per_event",
            ),
            models.UniqueConstraint(
                fields=["event_day"],
                condition=models.Q(is_primary=True),
                name="unique_primary_image_per_event_day",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "event_day", "sort_order"]),
        ]

    def __str__(self):
        scope = self.event_day.event_day_title if self.event_day else self.event.title
        return f"Image — {scope or 'untitled'}"

    def clean(self):
        super().clean()
        if self.event_day_id and self.event_day.owner_id != self.event_id:
            raise ValidationError({
                "event_day": "This event day belongs to a different event.",
            })

    def get_portal_id(self):
        try:
            return self.event.celebrant.portal.id
        except AttributeError:
            raise ValueError(
                f"EventImage for '{self.event_id}' has no celebrant or the celebrant "
                "has no portal. Cannot generate upload path."
            )
