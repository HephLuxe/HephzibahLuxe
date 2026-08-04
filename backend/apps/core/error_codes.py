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
RATE_LIMITED = "rate_limited"
TOKEN_EXPIRED = "token_expired"
TOKEN_INVALID = "token_invalid"
INTERNAL_ERROR = "internal_error"
