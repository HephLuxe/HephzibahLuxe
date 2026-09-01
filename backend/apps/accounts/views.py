import logging

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from django_ratelimit.exceptions import Ratelimited
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.core.error_codes import (
    NOT_FOUND,
    PASSWORD_RESET_REQUIRED,
    TOKEN_INVALID,
    VALIDATION_ERROR,
)
from apps.core.pagination import UserPageNumberPagination
from apps.core.ratelimit import resolve_client_ip
from apps.core.utils import save_with_attribution

from ..core.permissions import IsStaffOrSuperuser, enforce, is_staff_or_superuser
from . import developers, login_guard, services
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
from .utils import create_password_reset_token, send_password_reset_email

logger = logging.getLogger(__name__)

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
    # `pk` as a tie-breaker, required now that this list is paged: ordering by
    # date_joined alone leaves rows sharing a timestamp in an order Postgres
    # does not promise to repeat, so a row could appear on two pages and another
    # on none. Harmless while the whole list came back in one response.
    qs = qs.order_by(ordering, "pk")

    # ALWAYS paginated, like GET /inquiries/. This used to serialise the entire
    # directory in one response — every account's email, name, role, active
    # state, last login and portal id. Rate limiting caps how MANY requests a
    # caller makes, not how much each one hands over, so a compromised staff
    # token needed exactly one request for the whole platform's user list.
    # Bounding the page is the control that actually applies here.
    #
    # ?page= walks the rest and ?page_size= widens it to the paginator's max, so
    # nothing is unreachable — it just costs one request per page.
    #
    # The envelope gains `next`/`previous` beside the `count` and `results` this
    # already returned, so a caller reading those two keys still parses the
    # response; it receives one page instead of everything.
    paginator = UserPageNumberPagination()
    page = paginator.paginate_queryset(qs, request)
    serializer = UserListSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)


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
    """
    One user by email. Readable by staff/superusers, or by that user themselves.

    A caller who may not view this user gets **404, not 403** — deliberately, and
    it is the whole reason this view does not use the usual
    get-then-``enforce()`` shape. Looking the row up first and rejecting after
    turns the status code into a yes/no oracle: 404 means "no account with that
    address", 403 means "there is one, it just isn't yours". Any authenticated
    caller, including a client account with no privileges at all, could walk a
    list of candidate addresses and learn which ones have accounts here — every
    other client, every staff member, the superuser. Collapsing both cases into
    the same 404 says only "nothing here for you", which is all an unauthorised
    caller is entitled to know.

    Nothing is lost for legitimate callers: staff and the user themselves are
    authorised, so they never see the collapsed response.
    """
    User = get_user_model()
    user = User.objects.filter(email=email).first()

    # One combined check, so "does not exist" and "exists but not yours" are
    # indistinguishable from outside. Kept as a single condition rather than two
    # branches returning the same thing, so no later edit can split them apart
    # and quietly reintroduce the oracle.
    if user is None or not (is_staff_or_superuser(request.user) or request.user == user):
        return _error("User not found.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    return Response(UserSerializer(user).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def register_user(request):
    """
    Admin-only endpoint - only admins/staff can register users.
    Generates temporary password and sends credentials email.
    """
    enforce(is_staff_or_superuser(request.user), "You don't have permission to register users. Only admins can register users.")

    # context= is load-bearing, not boilerplate: AdminUserCreationSerializer's
    # validate_role/validate_email read request.user to decide whether the
    # caller may mint a developer. Without it every staff account could.
    serializer = AdminUserCreationSerializer(data=request.data, context={"request": request})
    if not serializer.is_valid():
        return _error("Invalid user data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    user = save_with_attribution(serializer, request.user)
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
    """Login. Returns tokens + user data, and owns its own rate limiting.

    Unlike every other limited endpoint this one is NOT wrapped at the URL, and
    the reason is the whole of ADR-0002: the decorator increments before the view
    knows the outcome, so correct logins were spending anti-brute-force budget
    and an office behind one NAT could lock itself out while doing nothing wrong.
    Here the tiers are checked on the way in and counted only when authentication
    actually failed.

    Order of operations matters and is deliberate:

    1. **Check the rate tiers.** Full -> 429 before any password hashing happens.
    2. **Refuse a locked account before checking its password.** It has to be
       this way round: verifying first would leave guessing unbounded (and would
       spend ~68ms of PBKDF2 per guess). The cost is that a correct password
       cannot rescue a locked account — the recovery path is the password reset,
       whose code goes to an inbox an attacker cannot read.
    3. **Authenticate.**
    4. **On failure** — count it against every tier and against the account, then
       report either invalid credentials or, once the account has just run out of
       attempts, that a password reset is now required.
    5. **On success** — clear the account's failure run, so ordinary fumbling
       never accumulates toward the ceiling.

    Enumeration note, accepted deliberately: `password_reset_required` is only
    ever returned for an address that has an account, so five failures reveal
    whether one exists. The same trade is already made by
    `utils.verify_reset_code`, which answers TOO_MANY_ATTEMPTS_MESSAGE only for a
    real token. Bounded by the account tiers for addresses with no account.
    """

    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        email = ""
        if isinstance(request.data, dict):
            email = (request.data.get("email") or "").strip().lower()
        tiers = login_guard.login_tiers(email)

        full = login_guard.first_full_tier(request, tiers)
        if full is not None:
            # WHICH tier fired is the one thing the shared 429 renderer cannot
            # report — it sees only the exception. Login is the only endpoint
            # with four tiers on one path, so without this the obvious question
            # ("is it the burst, the account, or the day?") has no answer in the
            # logs. Kept at INFO so the alertable WARNING stays the single
            # `event="rate_limited"` line apps/core/views.ratelimited emits.
            logger.info(
                "Login tier exhausted.",
                extra={
                    "event": "login_tier_exhausted",
                    "tier": full["group"],
                    "retry_after": full["retry_after"],
                },
            )
            # The renderer sees only the exception, so the wait has to travel on
            # it. Without this a caller blocked by auth_login_daily is told to
            # come back in 60 seconds and is refused for up to a day.
            exc = Ratelimited()
            exc.retry_after = full["retry_after"]
            raise exc

        # Put a drifted developer row back BEFORE anything reads it. Django's
        # auth backend and SimpleJWT both check `is_active` on the row and would
        # refuse a developer whom a queryset.update() had deactivated — the one
        # demotion path that bypasses User.save(). A set-membership test for
        # every other address, so ordinary logins pay nothing.
        developers.repair_by_email(email)

        # get_user_model() locally, matching the rest of this module.
        user = get_user_model().objects.filter(email=email).first() if email else None

        # EITHER counter at its ceiling means locked. The email-keyed one exists
        # so this decision does not depend on whether an account exists — see
        # login_guard for the enumeration oracle that closes.
        if login_guard.email_is_locked(email) or (user is not None and user.login_locked()):
            return self._reset_required(email, user)

        try:
            response = super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            login_guard.record_failed_attempt(request, tiers)
            # Always counted, account or not — that symmetry IS the fix.
            login_guard.record_email_failure(email)
            if user is not None:
                user.record_failed_login()
            if login_guard.email_is_locked(email) or (user is not None and user.login_locked()):
                return self._reset_required(email, user)
            raise

        # Authentication succeeded. Clear the run — the property that keeps a
        # per-account counter from becoming a lockout weapon.
        login_guard.clear_email_failures(email)
        if user is not None:
            user.reset_failed_logins()
        return response

    @staticmethod
    def _reset_required(email, user):
        """The 401 that points a locked-out account holder at the reset flow.

        Byte-identical whether or not `email` has an account behind it. That is
        the whole point — a response that differed would be a user-enumeration
        oracle costing five wrong passwords. The LOG line does distinguish the
        two, because the log is not something an attacker can read and "which
        real account is under attack" is the question an operator has.
        """
        logger.warning(
            "Login refused: out of attempts.",
            extra={
                "event": "login_account_locked",
                "user_id": str(user.id) if user is not None else None,
                "has_account": user is not None,
                "failed_login_count": user.failed_login_count if user is not None else None,
                "email_failure_count": login_guard.email_failure_count(email),
            },
        )
        return _error(
            "Too many failed sign-in attempts. Reset your password to regain access.",
            PASSWORD_RESET_REQUIRED,
            status.HTTP_401_UNAUTHORIZED,
        )

    def handle_exception(self, exc):
        """Let ``Ratelimited`` escape DRF instead of becoming a 403.

        ``Ratelimited`` subclasses Django's ``PermissionDenied``, and DRF's
        handler maps that to **403**. Every other limited endpoint avoids this by
        raising *outside* DRF — the decorator sits on the URL, above dispatch —
        which is exactly what apps/accounts/urls.py's ``_rl`` docstring warns
        about. This view raises from inside, so it has to opt out by hand.

        Re-raising propagates the exception out of ``dispatch`` to Django, where
        ``RatelimitMiddleware`` renders ``settings.RATELIMIT_VIEW``. That is the
        point: login's 429 is then produced by the same code path as every other
        endpoint's, envelope and ``Retry-After`` included, rather than a
        hand-rolled copy that could drift.
        """
        if isinstance(exc, Ratelimited):
            raise exc
        return super().handle_exception(exc)


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
            # apps.core.ratelimit is the single source of truth for the client
            # address; this view used to resolve it itself and took the LEFTMOST
            # X-Forwarded-For entry, which is the one the caller supplies. Two
            # things followed from that. The audit column recorded whatever the
            # caller typed, so the one field meant to answer "who asked for this
            # reset" was worthless. And because the value reached a varchar(45)
            # column unvalidated, a header longer than 45 characters raised a
            # DataError here — which, since the except below only catches
            # DoesNotExist, escaped as a 500 for an email that EXISTS while an
            # unknown email still returned 200. That made the "always return
            # success" guarantee directly above a user-enumeration oracle.
            # resolve_client_ip returns a validated address, so it is at most 45
            # characters by construction and the column can no longer overflow.
            ip_address = resolve_client_ip(request)
            # The plaintext code is returned, never stored — only its hash is
            # persisted, so this is the one moment it exists. Mail it here or it
            # is gone (see accounts.utils.create_password_reset_token).
            _token, code = create_password_reset_token(user, ip_address)
            send_password_reset_email(user, code)
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
        # Completing a reset is the documented way OUT of a login lockout
        # (ADR-0002): the code was delivered to an inbox an attacker cannot read,
        # so finishing this flow proves ownership just as the right password
        # does. Without it an account that reached MAX_FAILED_LOGINS would stay
        # locked until the 24h window aged out, and the "reset your password"
        # message the login endpoint returns would be a lie.
        #
        # Only here, not in ForcePasswordChangeView: that one requires an
        # authenticated caller, so a successful login has already cleared the run.
        user.failed_login_count = 0
        user.failed_login_at = None
        user.save()
        # Both counters, or the email-keyed one would keep the account locked
        # after a recovery the user just proved they were entitled to.
        login_guard.clear_email_failures(user.email.lower().strip())

        token.is_used = True
        token.used_at = timezone.now()
        token.save()

        return Response({"detail": "Password has been reset successfully."}, status=status.HTTP_200_OK)
