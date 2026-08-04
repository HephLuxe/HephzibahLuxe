"""
URL configuration for config project.

All application routes are mounted under the versioned prefix ``/api/v1/``.
Bump to ``/api/v2/`` as a parallel mount when a breaking revision is needed —
never mutate v1 in place. The Django admin lives outside the API prefix.

Canonical, human-readable route list: docs/API_CONTRACT.md.
"""
from django.contrib import admin
from django.urls import include, path

from apps.core.views import health_live, health_ready

# Every app is prefix-free internally; the version prefix is applied here once.
api_v1_patterns = [
    path('', include('apps.accounts.urls')),
    path('', include('apps.events.urls')),
    path('', include('apps.portal.urls')),
    path('', include('apps.contacts.urls')),
    path('', include('apps.meetings.urls')),
    path('', include('apps.documents.urls')),
    path('', include('apps.conversations.urls')),
    path('', include('apps.budgets.urls')),
    path('', include('apps.reminders.urls')),
    path('', include('apps.document_hub.urls')),
    path('', include('apps.notifications.urls')),
]

urlpatterns = [
    path('admin/', admin.site.urls),
    # Health probes live outside the API version prefix so uptime monitors and
    # the home-server control-panel wizard hit stable, unauthenticated paths.
    path('health/', health_live),
    path('health/ready/', health_ready),
    path('api/v1/', include(api_v1_patterns)),
]

# No local-disk media route: media is served from R2 (signed/public URLs) in
# every real environment, and from in-memory storage under tests — never off the
# filesystem, so there's nothing for Django to serve at MEDIA_URL.
