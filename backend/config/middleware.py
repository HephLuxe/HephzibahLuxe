from django.http import JsonResponse
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.core.error_codes import PERMISSION_DENIED


class ForcePasswordChangeMiddleware:
    """
    Middleware to enforce password change for users with force_password_change=True.

    Blocks all API endpoints except:
    - /api/v1/auth/force-password-change/ (to change password)
    - /api/v1/auth/token/ (to get JWT)
    - /api/v1/auth/token/refresh/ (to refresh JWT)
    - /api/v1/auth/token/logout/ (so a locked-out user can still log out)
    - /admin/ (Django admin)
    - Static/media files
    """

    # Allowed URL patterns that don't require password change. Must be kept in
    # sync with apps/accounts/urls.py — these were still the pre-/api/v1/
    # paths (/api/token/login, /api/auth/force-password-change/) after the
    # Phase 0 URL versioning move, which meant this middleware silently
    # blocked EVERY path for a force_password_change user, including the one
    # path that's supposed to let them fix it. Found while preparing manual
    # test data that exercises the real registration flow.
    ALLOWED_PATHS = [
        '/api/v1/auth/force-password-change/',
        '/api/v1/auth/token/',
        '/admin/',
    ]

    def __init__(self, get_response):
        self.get_response = get_response
        self.jwt_authenticator = JWTAuthentication()

    def __call__(self, request):
        # Skip middleware for allowed paths
        if self._is_allowed_path(request.path):
            return self.get_response(request)

        # Try to authenticate JWT token if present
        if not request.user.is_authenticated:
            try:
                # Check for Authorization header
                auth_header = request.META.get('HTTP_AUTHORIZATION', '')
                if auth_header.startswith('Bearer '):
                    # Authenticate the JWT token
                    user_auth = self.jwt_authenticator.authenticate(request)
                    if user_auth is not None:
                        request.user, _ = user_auth
            except (AuthenticationFailed, Exception):
                # If JWT auth fails, continue as unauthenticated
                pass

        # Skip middleware for unauthenticated requests
        if not request.user.is_authenticated:
            return self.get_response(request)

        # Check if user needs to change password
        if hasattr(request.user, 'force_password_change') and request.user.force_password_change:
            return JsonResponse(
                {
                    "detail": "Password change required. Please change your temporary password before accessing the platform.",
                    "code": PERMISSION_DENIED,
                    "force_password_change": True,
                    "password_change_url": "/api/v1/auth/force-password-change/"
                },
                status=403
            )

        return self.get_response(request)

    def _is_allowed_path(self, path):
        """Check if path is in allowed list"""
        # Check exact matches and prefixes
        for allowed_path in self.ALLOWED_PATHS:
            if path.startswith(allowed_path):
                return True

        # Allow static and media files
        if path.startswith('/static/') or path.startswith('/media/'):
            return True

        return False
