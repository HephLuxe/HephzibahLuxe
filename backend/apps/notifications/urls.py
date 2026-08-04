"""
apps/notifications/urls.py

Mounted prefix-free; config/urls.py applies the /api/v1/ version prefix.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("notifications/", views.list_notifications, name="list_notifications"),  # GET
]
