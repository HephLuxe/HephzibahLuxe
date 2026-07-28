"""
apps/document_hub/urls.py

Mounted under /api/v1/ in config/urls.py.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("document-hub/", views.get_hub, name="document-hub"),  # GET

    path("document-hub/defaults/", views.portal_defaults, name="document-hub-defaults"),  # GET|PATCH

    path("document-hub/documents/", views.create_document, name="document-hub-document-create"),  # POST
    path("document-hub/documents/<uuid:document_id>/", views.document_detail, name="document-hub-document-detail"),  # PATCH|DELETE

    path("document-hub/payment-schedule/", views.create_payment_schedule, name="payment-schedule-create"),  # POST
    path("document-hub/payment-schedule/<uuid:schedule_id>/", views.update_payment_schedule, name="payment-schedule-update"),  # PATCH|DELETE
    path("document-hub/payment-schedule/<uuid:schedule_id>/milestones/", views.add_milestone, name="milestone-create"),  # POST
    path("document-hub/milestones/<uuid:milestone_id>/", views.milestone_detail, name="milestone-detail"),  # PATCH|DELETE
    path("document-hub/milestones/<uuid:milestone_id>/mark-paid/", views.mark_milestone_paid, name="milestone-mark-paid"),  # PATCH

    path("document-hub/invoices/", views.create_invoice, name="invoice-create"),  # POST
    path("document-hub/invoices/<uuid:invoice_id>/", views.invoice_detail, name="invoice-detail"),  # PATCH|DELETE

    path("document-hub/receipts/", views.create_receipt, name="receipt-create"),  # POST
    path("document-hub/receipts/<uuid:receipt_id>/", views.receipt_detail, name="receipt-detail"),  # PATCH|DELETE
]
