from django.urls import path
from . import views

urlpatterns = [
    # Portal overview
    path("portal/", views.get_portal, name="portal-detail"),  # GET
    path("portal/update/", views.update_portal, name="portal-update"),  # PATCH

    # Phase management (staff only)
    path("portal/phase/", views.update_phase, name="portal-phase"),  # PATCH

    # Active event switching (staff only)
    path("portal/activate-event/", views.activate_event, name="portal-activate-event"),  # PATCH

    # Team assignments (per portal)
    path("portal/team/", views.list_portal_team, name="portal-team-list"),  # GET
    path("portal/team/assign/", views.assign_team_member, name="portal-team-assign"),  # POST
    path("portal/team/remove/", views.remove_team_member, name="portal-team-remove"),  # DELETE

    # Team member profiles (global, staff-managed)
    path("portal/team-members/", views.list_team_members, name="team-members-list"),  # GET
    path("portal/team-members/create/", views.create_team_member, name="team-members-create"),  # POST
    path("portal/team-members/<uuid:member_id>/", views.manage_team_member, name="team-members-manage"),  # PATCH|DELETE
]
