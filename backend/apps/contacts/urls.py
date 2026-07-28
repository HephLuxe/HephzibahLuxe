from django.urls import path
from . import views

urlpatterns = [
    path("event/<slug:event_slug>/contacts/", views.list_contacts, name="contacts-list"),  # GET
    path("event/<slug:event_slug>/contacts/summary/", views.contacts_summary, name="contacts-summary"),  # GET
    path("event/<slug:event_slug>/contacts/create/", views.create_contact, name="contacts-create"),  # POST
    path("event/<slug:event_slug>/contacts/<uuid:contact_id>/", views.contact_detail, name="contacts-detail"),  # GET|PATCH|DELETE
    path("contacts/lock/", views.toggle_contacts_lock, name="toggle_contacts_lock"),  # PATCH
    path("event/<slug:event_slug>/contacts/copy/", views.copy_contacts_from_day, name="contacts-copy"),  # POST
]
