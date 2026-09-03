"""
apps/document_hub/serializers.py
"""

import datetime
from decimal import Decimal

from rest_framework import serializers

from apps.core.serializers import AttributionSerializerMixin, PrivateFileURLField
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
    # `file` is write-only: uploads still target it (and validate_file below
    # still runs), but reads expose `file_url` — the endpoint that mints a
    # 60-second signed URL after an ownership check. Serializing the storage URL
    # directly handed the client a one-hour signature minted at page-render
    # time, which went stale in an open tab and kept working if forwarded. See
    # apps/core/filelinks.py.
    file_url = PrivateFileURLField("client-document")

    class Meta:
        model = ClientDocument
        fields = [
            "id", "category", "category_display", "reference_code",
            "title", "description", "file", "file_url", "is_signed", "signed_on",
            "order", "created_at", "updated_at",
            "created_by_display", "last_updated_by_display",
        ]
        # reference_code is system-generated (see document_hub.services /
        # signals) — read-only, never accepted from the client.
        read_only_fields = ["id", "reference_code", "created_at", "updated_at"]
        extra_kwargs = {"file": {"write_only": True}}

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

    balance = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)

    class Meta:
        model = PaymentMilestone
        fields = [
            "id", "label", "percentage", "amount", "amount_paid", "balance",
            "due_date", "paid_on", "status", "status_display", "order",
            "created_by_display", "last_updated_by_display",
        ]
        # amount_paid and status are DERIVED from the milestone's invoices
        # (services.sync_milestone_from_invoices), so accepting either here
        # would let a PATCH write a figure the next invoice edit silently
        # overwrites. Move the money by paying the invoice, or by
        # PATCH .../mark-paid/ for a milestone that has none.
        read_only_fields = ["id", "amount_paid", "balance", "status", "paid_on"]


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
        # The OUTSTANDING figure, not the milestone's full amount. On a
        # part-paid milestone the full amount is money the client has already
        # partly sent, and billing it again is the tile telling them to
        # overpay.
        milestone = obj.next_payment_milestone
        return milestone.balance if milestone else None

    def get_next_payment_due_date(self, obj: PaymentSchedule) -> datetime.date | None:
        milestone = obj.next_payment_milestone
        return milestone.due_date if milestone else None


class InvoiceSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    # Which milestone this invoice bills for. Writable by id so staff can point
    # a manually-raised invoice at a milestone; `milestone_label` is the
    # read-side convenience so the invoices table can show it without a lookup.
    milestone = serializers.PrimaryKeyRelatedField(
        queryset=PaymentMilestone.objects.all(), required=False, allow_null=True,
    )
    milestone_label = serializers.CharField(source="milestone.label", read_only=True, default=None)
    file_url = PrivateFileURLField("invoice")

    class Meta:
        model = Invoice
        fields = [
            "id", "invoice_number", "milestone", "milestone_label",
            "issued_on", "due_on",
            "amount", "status", "status_display", "file", "file_url", "created_at",
            "created_by_display", "last_updated_by_display",
        ]
        # invoice_number is system-generated — read-only.
        read_only_fields = ["id", "invoice_number", "created_at"]
        # due_on is NULL-able on the model purely so issue_invoices_for_schedule
        # can raise an invoice for a milestone with no agreed date yet. An API
        # caller writing an invoice by hand has a date in mind, so it stays
        # required here rather than silently landing as null.
        extra_kwargs = {
            "due_on": {"required": True, "allow_null": False},
            "file": {"write_only": True},
        }

    def validate_file(self, value):
        return validate_document(value)


class ReceiptSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
    file_url = PrivateFileURLField("receipt")

    class Meta:
        model = Receipt
        fields = ["id", "receipt_number", "paid_on", "payment_for", "amount", "file", "file_url", "created_at", "created_by_display", "last_updated_by_display",
        ]
        # receipt_number is system-generated — read-only.
        read_only_fields = ["id", "receipt_number", "created_at"]
        extra_kwargs = {"file": {"write_only": True}}

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
