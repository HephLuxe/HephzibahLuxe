from rest_framework.serializers import ModelSerializer, EmailField
from rest_framework import serializers

from apps.core.serializers import AttributionSerializerMixin

from .models import Event, EventDay

# AttributionSerializerMixin supplies created_by_display / last_updated_by_display
# and strips the raw actor FK ids — without it, `fields = '__all__'` below would
# serialize last_updated_by as a bare user pk (the old `last_updated_by: 1`).

class EventSerializer(AttributionSerializerMixin, ModelSerializer):

    celebrant = EmailField(source='celebrant.email', read_only=True)

    class Meta:
        model = Event
        fields = '__all__'
        read_only_fields = ['id','slug', 'created_by', 'last_updated_by', 'created_at', 'updated_at']

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
        

class EventDaySerializer(AttributionSerializerMixin, ModelSerializer):
    owner = serializers.CharField(source="owner.title", read_only=True)
    venue_booking_status_display = serializers.CharField(
        source="get_venue_booking_status_display", read_only=True
    )

    class Meta:
        model = EventDay
        fields = '__all__'
        read_only_fields = ['id', 'owner', 'created_by', 'last_updated_by', 'created_at', 'updated_at']
        