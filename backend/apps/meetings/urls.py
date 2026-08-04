from django.urls import path
from . import views

urlpatterns = [
    # Meetings
    path("meetings/", views.list_meetings, name="list_meetings"),  # GET
    path("meetings/phases/", views.phase_summary, name="meetings-phase-summary"),  # GET
    path("meetings/create/", views.create_meeting, name="create_meeting"),  # POST
    path("meetings/<uuid:meeting_id>/", views.meeting_detail, name="meeting_detail"),  # GET|PATCH|DELETE
    path("meetings/<uuid:meeting_id>/ics/", views.meeting_ics, name="meeting_ics"),  # GET
    path("meetings/<uuid:meeting_id>/status/", views.update_meeting_status, name="update_meeting_status"),  # PATCH
    path("meetings/<uuid:meeting_id>/notes/", views.add_meeting_notes, name="add_meeting_notes"),  # POST

    # Prep items
    path("meetings/<uuid:meeting_id>/prep/", views.add_prep_item, name="add_prep_item"),  # POST
    path("meetings/<uuid:meeting_id>/prep/<uuid:item_id>/", views.prep_item_detail, name="prep_item_detail"),  # GET|PATCH|DELETE

    # Prep item fields
    path("meetings/<uuid:meeting_id>/prep/<uuid:item_id>/fields/", views.add_prep_field, name="add_prep_field"),  # POST
    path("meetings/<uuid:meeting_id>/prep/<uuid:item_id>/fields/<uuid:field_id>/", views.prep_field_detail, name="prep_field_detail"),  # PATCH|DELETE
    path("meetings/<uuid:meeting_id>/prep/<uuid:item_id>/fields/<uuid:field_id>/respond/", views.respond_to_field, name="respond_to_field"),  # POST|DELETE

    # A single uploaded file on a file_upload field (remove one, keep the rest).
    # upload_id is an int — PrepItemFileUpload still has the default BigAutoField
    # PK. Enumerating it buys nothing: the lookup is scoped upload→field→item→
    # meeting and gated by the same ownership check as respond.
    path(
        "meetings/<uuid:meeting_id>/prep/<uuid:item_id>/fields/<uuid:field_id>/uploads/<int:upload_id>/",
        views.prep_upload_detail, name="prep_upload_detail",
    ),  # DELETE
]
