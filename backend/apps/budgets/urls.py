# apps/budget/urls.py

from django.urls import path

from . import views

urlpatterns = [
    # Budget record
    path("event/<slug:event_slug>/budget/", views.budget_overview, name="budget-overview"),  # GET
    path("event/<slug:event_slug>/budget/create/", views.budget_create, name="budget-create"),  # POST
    path("event/<slug:event_slug>/budget/update/", views.budget_update, name="budget-update"),  # PATCH|DELETE

    # Categories
    path("event/<slug:event_slug>/budget/categories/", views.category_create, name="category-create"),  # POST
    path("event/<slug:event_slug>/budget/categories/<uuid:category_id>/", views.category_detail, name="category-detail"),  # PATCH|DELETE

    # Payment history
    path("event/<slug:event_slug>/budget/payments/", views.payment_history, name="payment-history"),  # GET
    path("event/<slug:event_slug>/budget/payments/create/", views.payment_create, name="payment-create"),  # POST
    path("event/<slug:event_slug>/budget/payments/<uuid:payment_id>/", views.payment_detail, name="payment-detail"),  # PATCH|DELETE
]
