"""
apps/reminders/urls.py

Mounted under /api/v1/ in config/urls.py.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("reminders/", views.list_reminders, name="list_reminders"),  # GET
    path("reminders/create/", views.create_reminder, name="create_reminder"),  # POST
    path("reminders/<uuid:reminder_id>/", views.reminder_detail, name="reminder_detail"),  # GET|PATCH|DELETE
    path("reminders/<uuid:reminder_id>/complete/", views.complete_reminder, name="complete_reminder"),  # PATCH
]
