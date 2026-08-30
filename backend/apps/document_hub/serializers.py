"""
apps/document_hub/serializers.py
"""

import datetime
from decimal import Decimal

from rest_framework import serializers

from apps.core.serializers import AttributionSerializerMixin
from apps.core.uploads import validate_document

from .models import (
    SIGNABLE_CATEGORIES,
    ClientDocument,
    Invoice,
    PaymentMilestone,
    PaymentSchedule,
    PortalDefaults,
    Receipt,
)


class ClientDocumentSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    category_display = serializers.CharField(source="get_category_display", read_only=True)

    class Meta:
        model = ClientDocument
        fields = [
            "id", "category", "category_display", "reference_code",
            "title", "description", "file", "is_signed", "signed_on",
            "order", "created_at", "updated_at",
            "created_by_display", "last_updated_by_display",
        ]
        # reference_code is system-generated (see document_hub.services /
        # signals) — read-only, never accepted from the client.
        read_only_fields = ["id", "reference_code", "created_at", "updated_at"]

    def validate_file(self, value):
        return validate_document(value)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Only the signable categories (Service Agreement / Quotation) are ever
        # signed or carry a reference code. Emitting is_signed=false /
        # signed_on=null / reference_code="" on an FAQ or Welcome Booklet reads
        # as "this FAQ is unsigned" rather than "signing doesn't apply here", so
        # drop them for the categories they're meaningless on. Same approach
        # EventSerializer uses to hide wedding-only name fields.
        if instance.category not in SIGNABLE_CATEGORIES:
            data.pop("is_signed", None)
            data.pop("signed_on", None)
            data.pop("reference_code", None)
        return data


class PaymentMilestoneSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = PaymentMilestone
        fields = [
            "id", "label", "percentage", "amount", "due_date", "paid_on",
            "status", "status_display", "order",
            "created_by_display", "last_updated_by_display",
        ]
        read_only_fields = ["id"]


class PaymentScheduleSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    """Payment Overview tiles + milestone tracker."""
    milestones = PaymentMilestoneSerializer(many=True, read_only=True)
    paid_to_date = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    remaining_balance = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    next_payment_due_amount = serializers.SerializerMethodField()
    next_payment_due_date = serializers.SerializerMethodField()

    class Meta:
        model = PaymentSchedule
        fields = [
            "id", "total_investment", "paid_to_date", "remaining_balance",
            "next_payment_due_amount", "next_payment_due_date",
            "milestones", "updated_at",
            "created_by_display", "last_updated_by_display",
        ]
        read_only_fields = ["id", "updated_at"]

    def get_next_payment_due_amount(self, obj: PaymentSchedule) -> Decimal | None:
        milestone = obj.next_payment_milestone
        return milestone.amount if milestone else None

    def get_next_payment_due_date(self, obj: PaymentSchedule) -> datetime.date | None:
        milestone = obj.next_payment_milestone
        return milestone.due_date if milestone else None


class InvoiceSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "invoice_number", "issued_on", "due_on",
            "amount", "status", "status_display", "file", "created_at",
            "created_by_display", "last_updated_by_display",
        ]
        # invoice_number is system-generated — read-only.
        read_only_fields = ["id", "invoice_number", "created_at"]

    def validate_file(self, value):
        return validate_document(value)


class ReceiptSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Receipt
        fields = ["id", "receipt_number", "paid_on", "payment_for", "amount", "file", "created_at", "created_by_display", "last_updated_by_display",
        ]
        # receipt_number is system-generated — read-only.
        read_only_fields = ["id", "receipt_number", "created_at"]

    def validate_file(self, value):
        return validate_document(value)


class PortalDefaultsSerializer(serializers.ModelSerializer):
    """
    The org-wide default templates staff configure once — cloned onto every new
    engagement (the 3 files) and portal (welcome_message). All fields optional so
    a PATCH can update just one template or the message.
    """
    class Meta:
        model = PortalDefaults
        fields = [
            "service_agreement_file", "welcome_booklet_file", "faq_file",
            "welcome_message", "updated_at",
        ]
        read_only_fields = ["updated_at"]
        extra_kwargs = {
            "service_agreement_file": {"required": False},
            "welcome_booklet_file": {"required": False},
            "faq_file": {"required": False},
            "welcome_message": {"required": False},
        }

    # Three separate hooks rather than one validate(): DRF only runs
    # validate_<field> for fields actually present in the payload, which is what
    # makes a PATCH of one template leave the other two untouched.
    def validate_service_agreement_file(self, value):
        return validate_document(value)

    def validate_welcome_booklet_file(self, value):
        return validate_document(value)

    def validate_faq_file(self, value):
        return validate_document(value)
