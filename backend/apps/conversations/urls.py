from django.urls import path

from . import views

urlpatterns = [
    path("conversations/", views.list_conversations, name="list_conversations"),  # GET
    path("conversations/tags/", views.list_tags, name="conversation-tags"),  # GET
    path("conversations/phases/", views.phase_summary, name="phase_summary"),  # GET
    path("conversations/create/", views.create_conversation, name="create_conversation"),  # POST
    path("conversations/<uuid:conversation_id>/", views.conversation_detail, name="conversation_detail"),  # GET|PATCH|DELETE
]
