from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers
from rest_framework.serializers import ModelSerializer
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()

class TimezoneField(serializers.CharField):
    """An IANA timezone name, or "" to inherit the platform default.

    Validated here rather than with `choices=`: the IANA database gains, renames
    and merges zones, and pinning ~600 names into the model would turn every
    tzdata update into a migration. `zoneinfo` already knows the current set, so
    ask it.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_blank", True)
        kwargs.setdefault("max_length", 64)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not value:
            return ""
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise serializers.ValidationError(
                f"{value!r} is not a known IANA timezone name (e.g. 'Africa/Lagos')."
            ) from None
        return value


class UserSerializer(ModelSerializer):
    """The read surface for an account (GET /users/me/, GET /users/<email>/).

    `timezone` is exposed so a client can see what it is set to; it is written
    through PATCH /users/me/update/ (UserUpdateSerializer) or by staff in the
    Django admin.
    """

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'date_joined', 'timezone']
        # email is read-only here too. This serializer is output-only today, so
        # that changes nothing — it is here so that wiring it to a PATCH later
        # cannot quietly reintroduce the unverified identity change that
        # UserUpdateSerializer documents at length.
        read_only_fields = ['id', 'date_joined', 'email']


class UserListSerializer(ModelSerializer):
    """
    Staff-facing row in the user directory (GET /users/). Deliberately separate
    from UserSerializer: it adds the fields a staff dashboard needs to route to
    a client (their portal id, onboarding state) without those leaking into the
    self-serve /users/me/ shape.

    `portal_id` is null for staff/admin accounts (no portal) and for a client
    whose portal signal hasn't run — the frontend uses it to decide whether a
    row is clickable through to a portal.
    """
    full_name = serializers.SerializerMethodField()
    portal_id = serializers.SerializerMethodField()
    deactivated_by_display = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "full_name",
            "role", "is_active", "force_password_change",
            "deactivated_at", "deactivated_by_display", "deactivation_reason",
            "portal_id", "date_joined", "last_login",
        ]
        read_only_fields = fields

    def get_full_name(self, obj) -> str:
        from apps.core.utils import user_display_name
        return user_display_name(obj)

    def get_deactivated_by_display(self, obj) -> str:
        """Who offboarded this user. Blank for active accounts — the three
        deactivation fields are cleared on reactivation."""
        from apps.core.utils import user_display_name
        return user_display_name(obj.deactivated_by)

    def get_portal_id(self, obj) -> str | None:
        # portal is a reverse OneToOne — may not exist. The view select_related's
        # it, so this costs no extra query.
        portal = getattr(obj, "portal", None)
        return str(portal.id) if portal else None


class UserResigtrationSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = ["id","email", "first_name", "last_name", "password"]
        extra_kwargs = {
            'password': {'write_only': True} # removes it from the response
        }

    def create(self, validated_data: dict) -> User:
        email = validated_data["email"]
        first_name = validated_data["first_name"]
        last_name = validated_data["last_name"]
        password = validated_data["password"]

        user = get_user_model()
        new_user = user.objects.create_user(email=email, first_name=first_name, last_name=last_name, password=password)
        #new_user.set_password(password)
        new_user.save()
        return new_user

class UserLoginSerializer(ModelSerializer):

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name']
        read_only_fields = fields

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Custom serializer to include user data and force_password_change flag in login response"""

    def validate(self, attrs: dict) -> dict:
        data = super().validate(attrs)

        # Add user data
        data['user'] = {
            'id': self.user.id,
            'email': self.user.email,
            'first_name': self.user.first_name,
            'last_name': self.user.last_name,
            'force_password_change': self.user.force_password_change,  # NEW FIELD
        }

        return data

class UserUpdateSerializer(ModelSerializer):
    """PATCH|PUT /users/me/update/ — an account editing itself.

    `timezone` is here rather than admin-only on purpose. It decides which
    calendar day this account's payment-due and meeting-prep digests are computed
    against (apps/core/timezones.py), and on a platform used worldwide the person
    who knows the answer is the account holder — a client in Auckland should not
    have to ask staff in Lagos to set it for them. Blank inherits
    settings.PLATFORM_DEFAULT_TIMEZONE.

    `email` is deliberately NOT writable here, and it is the one field on this
    model where self-service is the wrong default.
    -------------------------------------------------------------------------
    It is USERNAME_FIELD. Changing it changes the account's login identity and
    redirects every future password-reset code and credentials email with it —
    unverified, so a typo silently sends all of them to an address the account
    holder does not control, and the lockout is only discovered at the next
    reset, which is exactly when recovery is already needed.

    A second, quieter effect: notifications.views._recipient_q falls back to
    `recipient_email__iexact` (it must, so a lead who later becomes a client can
    still read the acknowledgement sent before their account existed). Rewriting
    your own email therefore rewrites which notification rows you can read.
    `unique=True` stops you taking a LIVE account's address, but a deleted
    user's is free.

    Accounts here are staff-provisioned (`register_user` is staff-only), so
    self-service was never the flow that created them — routing changes through
    staff costs a client nothing and puts a human identity check in front of a
    login-identity change. The Django admin edits this field.

    If self-service is wanted later, the shape is a verified change: a code to
    the NEW address to prove ownership, plus a notice to the OLD one so the
    original owner can react. That is a feature, not a widened serializer.
    """
    timezone = TimezoneField()

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'timezone']
        read_only_fields = ['id', 'email']

    def validate(self, attrs):
        """Reject a submitted email change rather than dropping it.

        `read_only_fields` alone would strip it silently: the caller gets a 200
        with the old address echoed back, believes the change landed, and finds
        out at the next password reset. A 400 that says where to go is the whole
        difference between a control and a trap.

        Only a DIFFERENT address is refused — a PUT that echoes the account's
        current email back (the natural shape of "load the object, edit one
        field, send it all") is not trying to change anything and must not be
        punished for it.
        """
        submitted = (self.initial_data or {}).get("email")
        if submitted and self.instance:
            if str(submitted).strip().lower() != self.instance.email.lower():
                raise serializers.ValidationError({
                    "email": (
                        "Your sign-in email cannot be changed here. Contact your "
                        "planning team to update it."
                    )
                })
        return attrs




class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        """Accept any well-formed address, whether or not it has an account.

        Deliberately does NOT reject an unknown email: a 400 here would make this
        endpoint a user-enumeration oracle. The view answers 200 either way and
        only sends mail if the account exists.

        There is intentionally no lookup at all — an earlier version fetched the
        user into an unused variable, which read like a check that had been
        forgotten rather than one that must not exist.
        """
        return value


class PasswordResetVerifySerializer(serializers.Serializer):
    """Validates email + code combination"""
    email = serializers.EmailField()
    code = serializers.CharField(max_length=6, min_length=6)

    def validate_code(self, value: str) -> str:
        """Validate that code is 6 digits"""
        if not value.isdigit():
            raise serializers.ValidationError("Code must contain only digits")
        if len(value) != 6:
            raise serializers.ValidationError("Code must be exactly 6 digits")
        return value

    def validate(self, data: dict) -> dict:
        """Validate the email + code combination"""
        from .utils import verify_reset_code

        is_valid, result = verify_reset_code(data['email'], data['code'])

        if not is_valid:
            raise serializers.ValidationError({"code": result})

        # Store the token for use in the view
        data['_token'] = result
        return data

class PasswordResetConfirmSerializer(serializers.Serializer):
    """Validates email + code + new password + confirm password"""
    email = serializers.EmailField()
    code = serializers.CharField(max_length=6, min_length=6)
    new_password = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'},
        min_length=8
    )
    confirm_password = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'},
        min_length=8
    )

    def validate_code(self, value: str) -> str:
        """Validate that code is 6 digits"""
        if not value.isdigit():
            raise serializers.ValidationError("Code must contain only digits")
        if len(value) != 6:
            raise serializers.ValidationError("Code must be exactly 6 digits")
        return value

    def validate(self, data: dict) -> dict:
        """Validate passwords match and code is valid"""
        # Check if passwords match
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError({
                "confirm_password": "Passwords do not match"
            })

        # Verify the reset code
        from .utils import verify_reset_code

        is_valid, result = verify_reset_code(data['email'], data['code'])

        if not is_valid:
            raise serializers.ValidationError({"code": result})

        # Store the token for use in the view
        data['_token'] = result
        return data


class AdminUserCreationSerializer(serializers.ModelSerializer):
    """
    Serializer for admin-only user creation.
    Generates temporary password and sends credentials email.
    """
    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'role']

    def create(self, validated_data: dict) -> User:
        """Create user with temporary password"""
        from .utils import generate_temporary_password, send_user_credentials_email

        # Generate random temporary password
        temp_password = generate_temporary_password()

        # Create user
        user = User.objects.create_user(
            email=validated_data['email'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', ''),
            password=temp_password,
            role=validated_data.get('role', 'client')
        )

        # Set force password change fields AFTER user creation
        user.force_password_change = True
        user.temporary_password_created_at = timezone.now()
        user.save()

        # Send credentials email
        send_user_credentials_email(user, temp_password)

        return user

class ForcePasswordChangeSerializer(serializers.Serializer):
    """
    Serializer for forced password change on first login.
    Validates new password and confirms passwords match.
    """
    new_password = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'},
        min_length=8
    )
    confirm_password = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'},
        min_length=8
    )

    def validate(self, data: dict) -> dict:
        """Validate passwords match"""
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError({
                "confirm_password": "Passwords do not match"
            })
        return data

    def validate_new_password(self, value: str) -> str:
        """Validate password strength using Django validators"""
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(list(e.messages))

        return value
