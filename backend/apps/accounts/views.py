from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.core.error_codes import NOT_FOUND, TOKEN_INVALID, VALIDATION_ERROR
from ..core.permissions import IsStaffOrSuperuser, enforce, is_staff_or_superuser
from .serializers import (
    AdminUserCreationSerializer,
    CustomTokenObtainPairSerializer,
    ForcePasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PasswordResetVerifySerializer,
    UserListSerializer,
    UserSerializer,
    UserUpdateSerializer,
)
from . import services
from .utils import create_password_reset_token, send_password_reset_email


# ── Envelope helper (see apps/core/exceptions.py for the exception-path half) ──

def _error(detail: str, code: str, http_status: int, errors: dict | None = None) -> Response:
    body: dict = {"detail": detail, "code": code}
    if errors:
        body["errors"] = errors
    return Response(body, status=http_status)


@api_view(['GET'])
def Home(request):
    return Response("working!!")


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def secure(request):
    return Response("Secure working!!")


###############################################     USER       ###############################################

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def UserInfo(request):
    return Response(UserSerializer(request.user).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def list_users(request):
    """
    Staff only: the user directory behind the admin dashboard.

    Query params (all optional):
      role=client|staff|admin   filter by role (repeatable: ?role=staff&role=admin)
      is_active=true|false      filter by active state
      search=<text>             case-insensitive match on email / first / last name
      ordering=<field>          any of the allowed sort fields below, `-` for desc
                                (default: newest first)

    Contact details are staff-only by policy (CLAUDE.md §9), which is why this
    endpoint is gated to staff rather than exposing a client-visible directory.
    """
    User = get_user_model()
    # select_related("portal") so UserListSerializer.get_portal_id doesn't fire
    # one query per row.
    qs = User.objects.select_related("portal").all()

    roles = request.query_params.getlist("role")
    if roles:
        qs = qs.filter(role__in=roles)

    is_active = request.query_params.get("is_active")
    if is_active is not None:
        qs = qs.filter(is_active=str(is_active).strip().lower() in {"true", "1", "yes"})

    search = (request.query_params.get("search") or "").strip()
    if search:
        qs = qs.filter(
            Q(email__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
        )

    # Allow-list the sort keys — an arbitrary ?ordering= would let a caller sort
    # by password/other internals and probe the table.
    allowed_ordering = {"date_joined", "last_login", "email", "first_name", "last_name", "role"}
    ordering = (request.query_params.get("ordering") or "-date_joined").strip()
    if ordering.lstrip("-") not in allowed_ordering:
        return _error(
            f"Invalid ordering. Allowed: {', '.join(sorted(allowed_ordering))} (prefix with '-' for descending).",
            VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST,
        )
    qs = qs.order_by(ordering)

    serializer = UserListSerializer(qs, many=True)
    return Response({"count": len(serializer.data), "results": serializer.data})


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def set_user_status(request, email):
    """
    Staff only: deactivate (offboard) or reactivate a user.

    PATCH /users/<email>/status/
        { "is_active": false, "reason": "Contract completed" }   -> offboard
        { "is_active": true }                                    -> restore

    Deliberately one symmetric endpoint rather than two: reversing an
    offboarding is the same call with `is_active: true`, so a UI toggle maps
    straight onto it and there's no "undo" path that can drift from the "do"
    path. `reason` is optional and only recorded when deactivating.

    Deactivation is immediate — SimpleJWT rejects an inactive user on every
    authenticated request, and outstanding refresh tokens are blacklisted. No
    data is deleted; see accounts.services.deactivate_user.
    """
    User = get_user_model()
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return _error("User not found.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    if "is_active" not in request.data:
        return _error(
            "is_active is required (true to reactivate, false to deactivate).",
            VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST,
        )

    raw = request.data.get("is_active")
    if isinstance(raw, bool):
        make_active = raw
    elif str(raw).strip().lower() in {"true", "1", "yes"}:
        make_active = True
    elif str(raw).strip().lower() in {"false", "0", "no"}:
        make_active = False
    else:
        return _error("is_active must be a boolean.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    # services raises ValidationError for self-deactivation — let it propagate to
    # custom_exception_handler rather than re-mapping it here.
    if make_active:
        result = services.reactivate_user(user, by=request.user)
        detail = "User reactivated." if result["changed"] else "User is already active."
    else:
        result = services.deactivate_user(user, by=request.user, reason=request.data.get("reason", ""))
        detail = (
            f"User deactivated. {result['revoked_tokens']} refresh token(s) revoked. "
            "No data was deleted — reactivate to restore access."
            if result["changed"] else "User is already deactivated."
        )

    return Response({"detail": detail, "user": UserListSerializer(result["user"]).data})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def UserInfowEmail(request, email):
    User = get_user_model()
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return _error("User not found.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    # Permission check: Only the user themselves, superuser or staff
    enforce(is_staff_or_superuser(request.user) or request.user == user, "You don't have permission to view this user's information")

    return Response(UserSerializer(user).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def register_user(request):
    """
    Admin-only endpoint - only admins/staff can register users.
    Generates temporary password and sends credentials email.
    """
    enforce(is_staff_or_superuser(request.user), "You don't have permission to register users. Only admins can register users.")

    serializer = AdminUserCreationSerializer(data=request.data)
    if not serializer.is_valid():
        return _error("Invalid user data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    user = serializer.save()
    return Response(
        {
            "detail": "User created successfully. Login credentials sent to user's email.",
            "user": {
                "id": user.id,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "role": user.role,
                "force_password_change": user.force_password_change,
            }
        },
        status=status.HTTP_201_CREATED
    )


@api_view(['PATCH', 'PUT'])
@permission_classes([IsAuthenticated])
def update_user(request):
    partial = request.method == 'PATCH'
    serializer = UserUpdateSerializer(request.user, data=request.data, partial=partial)
    if not serializer.is_valid():
        return _error("Invalid user data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    serializer.save()
    return Response(serializer.data)


class ForcePasswordChangeView(APIView):
    """
    Force password change for users with temporary passwords.
    POST /api/v1/auth/force-password-change/

    Body: {
        "new_password": "SecurePassword123",
        "confirm_password": "SecurePassword123"
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        if not user.force_password_change:
            return _error("Password change not required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

        serializer = ForcePasswordChangeSerializer(data=request.data)
        if not serializer.is_valid():
            return _error("Invalid password data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

        new_password = serializer.validated_data['new_password']
        user.set_password(new_password)
        user.force_password_change = False
        user.temporary_password_created_at = None
        user.save()

        return Response(
            {"detail": "Password changed successfully. You now have full access to the platform."},
            status=status.HTTP_200_OK
        )


class MyTokenObtainPairView(TokenObtainPairView):
    # returns tokens + user data
    serializer_class = CustomTokenObtainPairSerializer


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return _error("Refresh token is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            return _error("Invalid or expired token.", TOKEN_INVALID, status.HTTP_400_BAD_REQUEST)
        # Anything else is a real bug, not an expected auth failure — let it
        # propagate to custom_exception_handler instead of masking it as a 400.

        return Response({"detail": "Successfully logged out."}, status=status.HTTP_200_OK)


################################################## Password Reset #################################################################################################

class PasswordResetRequestView(APIView):
    """
    Request a password reset code.
    POST /api/v1/auth/password-reset/request/

    Body: { "email": "user@example.com" }
    """
    permission_classes = []  # Public endpoint

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error("Invalid request.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

        email = serializer.validated_data['email']
        User = get_user_model()

        # Always return success to prevent user enumeration.
        response_message = {"detail": "If the email exists, a reset code has been sent."}

        try:
            user = User.objects.get(email=email)
            ip_address = self.get_client_ip(request)
            token = create_password_reset_token(user, ip_address)
            send_password_reset_email(user, token.code)
        except User.DoesNotExist:
            pass  # user doesn't exist, but don't reveal this

        return Response(response_message, status=status.HTTP_200_OK)


class PasswordResetVerifyView(APIView):
    """
    Verify the reset code (without resetting password yet).
    POST /api/v1/auth/password-reset/verify/

    Body: { "email": "user@example.com", "code": "123456" }
    """
    permission_classes = []  # Public endpoint

    def post(self, request):
        serializer = PasswordResetVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return _error("Invalid or expired code.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

        return Response(
            {"detail": "Code is valid. You may now reset your password."},
            status=status.HTTP_200_OK
        )


class PasswordResetConfirmView(APIView):
    """
    Confirm password reset with code and new password.
    POST /api/v1/auth/password-reset/confirm/

    Body: {
        "email": "user@example.com",
        "code": "123456",
        "new_password": "newSecurePassword123",
        "confirm_password": "newSecurePassword123"
    }
    """
    permission_classes = []  # Public endpoint

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        if not serializer.is_valid():
            return _error("Invalid or expired code.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

        email = serializer.validated_data['email']
        new_password = serializer.validated_data['new_password']
        token = serializer.validated_data['_token']  # Retrieved during validation

        User = get_user_model()
        user = User.objects.get(email=email)

        user.set_password(new_password)
        user.force_password_change = False
        user.temporary_password_created_at = None
        user.save()

        token.is_used = True
        token.used_at = timezone.now()
        token.save()

        return Response({"detail": "Password has been reset successfully."}, status=status.HTTP_200_OK)
