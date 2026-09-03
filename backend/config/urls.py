"""
URL configuration for config project.

All application routes are mounted under the versioned prefix ``/api/v1/``.
Bump to ``/api/v2/`` as a parallel mount when a breaking revision is needed —
never mutate v1 in place. The Django admin lives outside the API prefix.

Canonical, human-readable route list: docs/API_CONTRACT.md.
"""
from django.contrib import admin
from django.urls import include, path

from apps.core.admin_files import admin_private_file
from apps.core.admin_login import guarded_admin_login
from apps.core.views import health_live, health_ready

# Every app is prefix-free internally; the version prefix is applied here once.
api_v1_patterns = [
    path('', include('apps.core.urls')),
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
    path('', include('apps.inquiries.urls')),
]

urlpatterns = [
    # MUST come before 'admin/'. Django resolves in order, so this claims
    # /admin/login/ and admin.site.urls never sees it — which is how the admin's
    # sign-in form gets the same rate limits and five-strike account lock as
    # POST /api/v1/auth/token/. Without it the admin was an unlimited,
    # unlockable second door onto every is_staff account, and `role=admin` means
    # is_superuser. See apps/core/admin_login.py.
    #
    # reverse('admin:login') still resolves, and still produces '/admin/login/',
    # so every "you must log in first" redirect lands here too.
    path('admin/login/', guarded_admin_login, name='admin_login'),
    path('admin/', admin.site.urls),
    # Health probes live outside the API version prefix so uptime monitors and
    # the home-server control-panel wizard hit stable, unauthenticated paths.
    path('health/', health_live),
    path('health/ready/', health_ready),
    # Staff download links for the private storage tier. Deliberately OUTSIDE
    # the /api/v1/ prefix: this is admin plumbing on session auth, not part of
    # the API contract, and DRF's JWT-only authentication would reject an
    # admin's session cookie anyway. See apps/core/admin_files.py.
    path('admin-files/<str:file_type>/<str:obj_id>/', admin_private_file, name='admin_private_file'),

    path('api/v1/', include(api_v1_patterns)),
]

# No local-disk media route: media is served from R2 (signed/public URLs) in
# every real environment, and from in-memory storage under tests — never off the
# filesystem, so there's nothing for Django to serve at MEDIA_URL.
