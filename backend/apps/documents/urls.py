from django.urls import path
from . import views

urlpatterns = [
    path("documents/", views.list_portal_documents, name="list-portal-documents"),  # GET
]
