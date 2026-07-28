from rest_framework.serializers import ModelSerializer
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework import serializers
from django.utils import timezone


User = get_user_model()

class UserSerializer(ModelSerializer):

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'date_joined']
        read_only_fields = ['id', 'date_joined']


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
    class Meta:
        model = User
        fields =['id', 'email', 'first_name', 'last_name']
        read_only_fields = ['id']




class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        """Validate that email exists in database"""
        User = get_user_model()
        try:
            user = User.objects.get(email=value)
        except User.DoesNotExist:
            # Don't reveal if email exists (security: prevent user enumeration)
            # Return success message either way
            pass
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
