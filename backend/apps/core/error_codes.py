"""
apps/core/error_codes.py

Machine-readable error codes returned in the ``code`` field of every error
envelope (see apps/core/exceptions.py and Part 7 §E of the audit/plan).

Frontends branch on ``code``, never on the human-readable ``detail`` string.
Add new codes here as constants — never inline string literals in views.
"""

AUTHENTICATION_REQUIRED = "authentication_required"
INVALID_CREDENTIALS = "invalid_credentials"
PERMISSION_DENIED = "permission_denied"
NOT_FOUND = "not_found"
VALIDATION_ERROR = "validation_error"
INVALID_TRANSITION = "invalid_transition"
CONTACTS_LOCKED = "contacts_locked"
EVENT_DETAILS_LOCKED = "event_details_locked"
CONFIRMATION_REQUIRED = "confirmation_required"
# Two DIFFERENT limiters can produce a 429 and a frontend needs to tell them
# apart, because the remedy differs. RATE_LIMITED is a per-endpoint limit this
# caller tripped themselves — retry after the window and it clears.
# THROTTLED_GLOBAL is the shared ceiling on all unauthenticated traffic from one
# IP, so it may have been exhausted by somebody else behind the same NAT and
# retrying sooner will not help. Both used to report "rate_limited", which meant
# a 429 could not be attributed to either one in the logs or in the UI.
RATE_LIMITED = "rate_limited"
THROTTLED_GLOBAL = "throttled_global"
# Login refused because the account has accumulated too many consecutive failed
# attempts (User.MAX_FAILED_LOGINS). Distinct from INVALID_CREDENTIALS because
# the remedy is different and the frontend must say so: retrying cannot help,
# the account holder has to complete a password reset. See ADR-0002.
PASSWORD_RESET_REQUIRED = "password_reset_required"
# No TOKEN_EXPIRED. SimpleJWT raises InvalidToken for an expired token and a
# malformed one alike, so custom_exception_handler maps both to TOKEN_INVALID and
# a `token_expired` constant could never be emitted. Publishing a code a client
# can never receive is worse than not having it — a frontend writes a branch that
# never runs. The distinction is not needed either: the frontend refreshes on any
# 401 and signs out only when the refresh itself fails.
TOKEN_INVALID = "token_invalid"
INTERNAL_ERROR = "internal_error"
