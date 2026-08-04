from django.conf import settings
from django.db import models
from django.db.models import Q, F

# Create your models here.

class InquiryForm(models.Model):
    EVENT_TYPE = [
        ("Birthday", "Birthday Party"),
        ("Wedding", "Wedding"),
        ("Corporate", "Corporate Event"),
        ("Social Events", "Social Events"),
        ("Others", "Others"),
    ]
    CONTACT_MODE = [
        ("Email", "Email"),
        ("Phone Number", "Phone Number"),
    ]
    
    first_name = models.CharField( max_length=255)
    last_name = models.CharField( max_length=255)
    email = models.EmailField(max_length=255)
    phone_number = models.CharField(max_length=20)
    contact_mode = models.CharField(max_length=12, choices=CONTACT_MODE, blank = True, null=True)
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE, blank = True, null=True)
    preferred_start_date = models.DateField(null=True)
    preferred_end_date = models.DateField(null=True)
    desired_location = models.CharField(max_length=255)
    budget = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    details = models.TextField(blank=True, null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(preferred_end_date__gte=F('preferred_start_date')),
                name='valid_preferred_date_range'
            )
        ]
    
    def __str__(self):
        event = self.event_type if self.event_type else "Event Inquiry"
        return f"{self.first_name} {self.last_name} - {event} @ {self.desired_location}"