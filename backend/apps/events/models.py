import uuid

from django.db import models
from django.utils.text import slugify
from django.conf import settings
from apps.core.models import AttributedModel
from apps.core.storages import select_public_media_storage
from apps.core.utils import event_cover_upload_path, event_image_upload_path


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
    # max_length=500 (not Django's ImageField default of 100) — event_cover_upload_path
    # embeds portal UUID + event id + slug + covers/cover.ext, which comfortably
    # exceeds 100 chars; see docs/FAILURE_POINTS_AUDIT.md F6 (surfaced while
    # testing the fix there, after the F9 path change lengthened every path).
    featured_image = models.ImageField(upload_to=event_cover_upload_path, storage=select_public_media_storage, blank=True, null=True, max_length=500)
    event_type = models.CharField(max_length=255, choices=EVENT_TYPE, blank = True, null=True)
    description = models.TextField(blank=True, null=True)
    
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

class EventDay(AttributedModel):
    # UUID, not the default BigAutoField — this PK is exposed directly in
    # URLs (/event/<slug>/event_day/<id>/); a sequential int PK there lets
    # anyone enumerate every event day by incrementing the number.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="days")
    event_day_title = models.CharField(max_length=255, null=True, blank=True, help_text="kind of event wedding intoduction, traditional, white wedding...")
    date = models.DateField()
    start_time = models.TimeField(blank=True, null=True)
    end_time = models.TimeField(blank=True, null=True)
    content = models.TextField(blank=True, null=True)
    event_images = models.ImageField(upload_to=event_image_upload_path, storage=select_public_media_storage, blank=True, null=True, max_length=500)
    
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
    def day_number(self):
        """1-based position of this day among its event's days, ordered by date/start_time."""
        day_ids = list(self.owner.days.order_by("date", "start_time").values_list("id", flat=True))
        try:
            return day_ids.index(self.id) + 1
        except ValueError:
            return None