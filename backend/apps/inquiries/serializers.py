"""
apps/inquiries/serializers.py

InquiryCreateSerializer — input for the ONE public write path
(POST /api/v1/inquiries/). It has no output counterpart on purpose: the 201 is a
fixed message and never echoes the stored row back to an unauthenticated caller.

InquirySerializer — the read shape for the authenticated staff surface (list,
detail, status). Fully read-only; leads are business records.
"""

from django.utils import timezone
from rest_framework import serializers

from apps.core.serializers import AttributionSerializerMixin

from .models import InquiryForm


class InquiryCreateSerializer(serializers.ModelSerializer):
    """
    Public inquiry submission.

    Six model fields are nullable/blank at the database level (contact_mode,
    event_type, preferred_start_date, preferred_end_date, budget, details) but
    the live form asks for all of them. A plain ModelSerializer would inherit
    ``required=False`` from the model and silently store a half-filled lead, so
    every field the form collects is pinned required here — the model stays
    permissive (admin/shell entry, historical rows), the API does not.
    """

    # Not a model field: verified and discarded in the view, never persisted.
    recaptcha_token = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = InquiryForm
        fields = [
            "first_name", "last_name", "email", "phone_number", "contact_mode",
            "event_type", "preferred_start_date", "preferred_end_date",
            "desired_location", "budget", "details", "recaptcha_token",
        ]
        # allow_null=False alongside required=True: required alone only rejects
        # an ABSENT key, and DRF maps a null=True model field to allow_null=True,
        # so `"details": null` would still save the partial lead this exists to
        # prevent. `status` is deliberately absent from `fields` — it is
        # staff-only triage state, never settable from a public submit.
        extra_kwargs = {
            "first_name":           {"required": True},
            "last_name":            {"required": True},
            "email":                {"required": True},
            "phone_number":         {"required": True},
            # allow_blank=False on the two choice fields is NOT redundant:
            # both are blank=True on the model, and DRF keeps allow_blank on a
            # ChoiceField built from such a field, so `"contact_mode": ""` was
            # accepted and stored as "" — the same partial lead allow_null
            # closes from the other side.
            "contact_mode":         {"required": True, "allow_null": False, "allow_blank": False},
            "event_type":           {"required": True, "allow_null": False, "allow_blank": False},
            "preferred_start_date": {"required": True, "allow_null": False},
            "preferred_end_date":   {"required": True, "allow_null": False},
            "desired_location":     {"required": True},
            # details is blank=True on the model, which hands the serializer
            # allow_blank=True — `"details": ""` saved a lead with no brief at
            # all. The four other text fields are blank=False on the model, so
            # DRF already rejects "" on them.
            #
            # max_length is declared HERE and not on the model, for the same
            # reason as the rest of this block — but it is the one entry that is
            # not about half-filled leads. TextField carries no bound at all, so
            # details was the only unbounded input on the only unauthenticated
            # write path in the project; the four CharFields beside it hand DRF
            # their own max_length and were never open. Uncapped, the ceiling was
            # Django's 2.5MB DATA_UPLOAD_MAX_MEMORY_SIZE, and only by accident:
            # DRF parses from the raw stream and never touches request.body, so
            # the check fires solely because the burst tier's key callable
            # (apps.core.ratelimit._submitted_email) reads request.body first.
            #
            # One submission is also not one write. services.create_inquiry
            # copies details verbatim into the context JSONField of one
            # Notification row PER flagged staff member, and the retry sweep
            # re-reads and re-POSTs those rows to Brevo until it gives up — so a
            # multi-MB brief is amplified across durable rows AND fails the very
            # alert it is the payload of.
            #
            # 4000 characters is ~700 words, several times the length of a
            # thorough brief. Past that the answer is a conversation, which is
            # what the 201 already promises.
            "details":              {"required": True, "allow_null": False, "allow_blank": False, "max_length": 4000},
            # "Not sure yet" sends null — a sentinel 0/-1 would poison any
            # future budget reporting.
            "budget":               {"required": False, "allow_null": True},
        }

    def validate(self, data: dict) -> dict:
        start = data.get("preferred_start_date")
        end = data.get("preferred_end_date")

        # Without this an inverted range reaches the valid_preferred_date_range
        # CHECK constraint and surfaces as a 500 internal_error, not a 400.
        if start and end and end < start:
            raise serializers.ValidationError({
                "preferred_end_date": "The end date cannot be before the start date."
            })

        # localdate(), not date.today(): TIME_ZONE-aware, so a submission late in
        # the day is not rejected by a UTC off-by-one. Today itself is valid.
        if start and start < timezone.localdate():
            raise serializers.ValidationError({
                "preferred_start_date": "The start date cannot be in the past."
            })

        return data


class InquirySerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    """
    Full read shape for the staff surface (list / detail / status). Every stored
    field, all of it read-only: what a lead submitted is immutable, and the one
    mutable field — `status` — moves through its own PATCH sub-route so a
    general update can never quietly rewrite the client's own words.

    The three `choices` fields are each paired with their `_display` label so the
    frontend never hardcodes the value→label map (same pattern as
    apps/reminders/serializers.py and apps/contacts/serializers.py).

    `last_updated_by_display` is the "who moved this lead" surface — resolved at
    read time from the FK, so a staff rename propagates instead of freezing the
    name captured at write time. `created_by_display` is always "" (submissions
    are anonymous) and is carried anyway so the attribution block reads
    identically to every other model's. The mixin also strips the raw actor FK
    ids, which `fields` would otherwise emit as bare pks.
    """

    contact_mode_display = serializers.CharField(source="get_contact_mode_display", read_only=True)
    event_type_display = serializers.CharField(source="get_event_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = InquiryForm
        fields = [
            "id",
            "first_name", "last_name", "email", "phone_number",
            "contact_mode", "contact_mode_display",
            "event_type", "event_type_display",
            "preferred_start_date", "preferred_end_date",
            "desired_location", "budget", "details",
            "status", "status_display",
            "created_at", "created_by_display",
            "updated_at", "last_updated_by_display",
        ]
        read_only_fields = fields
