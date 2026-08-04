# Accounts App

This application manages user authentication, registration, password resets, and session tokens. It is built to prioritize security, using a custom User model and JWT for authentication.

## Key Features & Flows

### 1. Authentication & JWT
- Uses `rest_framework_simplejwt` for JWT generation.
- **Custom Token Serializer**: The `CustomTokenObtainPairSerializer` injects extra user data (like `first_name`, `last_name`, and the `force_password_change` flag) directly into the login token response payload.

### 2. Admin-Driven Registration
- **Flow**: Only staff/superusers can register new client accounts (via `register_user` view).
- **Temporary Password**: The `AdminUserCreationSerializer` automatically generates a cryptographically secure temporary password.
- **Async Delivery**: The credentials (with a secure login link) are sent via `utils.send_user_credentials_email(user, temporary_password)`, which calls `notifications.services.queue_notification()` — same pipeline as every other email in the platform (Celery, retry/backoff, Brevo template `user_credentials`, admin audit trail, toggleable via `NotificationTypeSettings`), ensuring the API response isn't blocked.

### 3. Forced Password Change
- When a new account is created, the `force_password_change` flag is set to `True`. 
- Upon first login, clients are required to set a permanent, secure password before they are granted full access to the platform.

### 4. Password Reset (3-Phase Flow)
Password resets use a secure 6-digit code system with a 15-minute expiration:
1. **Request (`/api/password-reset/request/`)**: Validates the email. To prevent user enumeration, it *always* returns a 200 OK regardless of whether the email exists. Generates a `PasswordResetToken` and sends the 6-digit code via `utils.send_password_reset_email(user, code)` — `notifications.services.queue_notification()`, Brevo template `password_reset`.
2. **Verify (`/api/password-reset/verify/`)**: Pre-checks the 6-digit code to ensure it matches the email and hasn't expired, returning a success message if valid.
3. **Confirm (`/api/password-reset/confirm/`)**: Accepts the code and the new password. It changes the password and immediately marks the token as used (`is_used=True`) to prevent replay attacks.

## File Structure & Logic Separation

- **`models.py`**: Contains the custom User model and `PasswordResetToken`.
- **`serializers.py`**: Handles all input validation (passwords, 6-digit codes) and creation logic. 
- **`views.py`**: Thin coordinators. They receive the request, pass data to serializers, and return responses. They do *not* contain heavy business logic or blocking I/O.
- **`utils.py`**: Contains helper functions for generating random codes/passwords, creating reset tokens, checking code validity, and queueing the two account emails (credentials, password reset) via `notifications.services.queue_notification()`. No dedicated `tasks.py` — email sending goes through the shared `notifications` app's Celery task (`send_notification_task`) rather than app-specific ones.

### 5. User directory — `GET /users/` (staff only)

Backs the staff dashboard's client list. Query params are all optional and
combine:

| Param | Notes |
|---|---|
| `role=` | repeatable — `?role=staff&role=admin` |
| `is_active=` | `true` / `false` |
| `search=` | case-insensitive over email / first / last name |
| `ordering=` | `-` for descending; default `-date_joined` |

Two things worth knowing: each row carries **`portal_id`** (null for staff, and
for a client whose portal signal hasn't run) so the frontend can link straight
through to the portal without a second lookup; and `ordering` is **allow-listed**
(`date_joined`, `last_login`, `email`, `first_name`, `last_name`, `role`) — an
arbitrary value is rejected with 400 rather than letting a caller sort by
internals to probe the table. The queryset `select_related("portal")` so the
`portal_id` field doesn't fire a query per row.

### 6. Deactivation / offboarding (reversible)

Offboarding is a **reversible state, not a delete** — see `services.py`.

```http
PATCH /api/v1/users/<email>/status/          (staff only)
{ "is_active": false, "reason": "Contract completed" }   → offboard
{ "is_active": true }                                    → restore
```

One symmetric endpoint on purpose: reversing is the same call with `true`, so a
UI toggle maps straight onto it and there's no separate "undo" path to drift.
The Django admin exposes the same thing as the *Deactivate / Re-activate* bulk
actions — **both route through `services.deactivate_user` / `reactivate_user`**,
so they can't diverge. Don't flip `is_active` directly; you'd skip the audit
stamp and the token revocation.

**Why not delete the user:** they're the FK target of their portal, their events,
and every `created_by`/`last_updated_by` stamp in the project. Deactivating keeps
all of it and is undoable; deleting would blank the attribution (`SET_NULL`) and
cascade the portal away.

**It takes effect immediately.** SimpleJWT's `CHECK_USER_IS_ACTIVE` defaults to
`True` and isn't overridden, so an inactive user is rejected on *every*
authenticated request — even an unexpired access token stops working, no waiting
for expiry. `deactivate_user` additionally blacklists outstanding **refresh**
tokens (the `token_blacklist` app is installed), because a refresh can otherwise
be exchanged for a new access token without loading the user.

**The audit trio** — `deactivated_at`, `deactivated_by`, `deactivation_reason` —
is **cleared on reactivation**, so a populated `deactivated_at` always means
"off right now". Exposed read-only in `GET /users/` as `deactivated_at` /
`deactivated_by_display` / `deactivation_reason`.

Gotchas:
- **You cannot deactivate yourself** — enforced in the service, so the API and
  the admin action both honour it (an easy misclick, painful to undo).
- **Both operations are idempotent.** Re-deactivating an already-inactive user
  is a no-op that *preserves the original reason* rather than overwriting who
  did it first.
- Reactivation does **not** un-blacklist old refresh tokens — the user simply
  logs in again for a fresh pair. Nothing else needs restoring.
- Covered by `tests.py::DeactivationTests`, including a test that asserts the
  user's portal and events survive an offboarding.
