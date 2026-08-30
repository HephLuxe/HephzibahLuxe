"""
apps/core/admin_login.py

Rate limiting and the account lock for **the Django admin's login form**.

The hole this closes
--------------------
``POST /api/v1/auth/token/`` carries four rate-limit tiers plus a five-strike
account lock. ``/admin/login/`` carried none of it, because it is neither a DRF
view (so ``DEFAULT_THROTTLE_CLASSES`` never runs) nor wrapped by the
``@ratelimit`` decorators in apps/accounts/urls.py. Two consequences:

  * unlimited password guesses against any ``is_staff`` account, and
  * an account locked out of the API could still sign in through the admin —
    which made the API lock decorative for exactly the accounts worth attacking.

``role=admin`` sets ``is_superuser=True`` in ``User.save()``, so the admin is the
whole operational control plane: users, notification toggles, feature settings,
reference counters, password-reset tokens.

Why a URL override and not an AdminSite subclass
------------------------------------------------
``config/urls.py`` lists ``admin/login/`` **before** ``admin/``. Django resolves
in order, so this view wins and ``admin.site.urls`` never sees the request. That
avoids swapping ``django.contrib.admin`` for a custom ``AdminConfig`` in
INSTALLED_APPS, and it leaves ``reverse('admin:login')`` working — it still
produces ``/admin/login/``, which now lands here, so every "you must log in
first" redirect is covered without touching a single admin registration.

Recovering from a lock, which is the part that matters
------------------------------------------------------
The admin's own "Release login lock" action is *inside* the admin, so a lock
applied here would otherwise be self-referential — the release button behind the
locked door. Two admin-independent paths exist and both must stay working:

  1. ``manage.py release_login_lock <email>`` from the platform shell.
  2. The password-reset flow, which clears the lock on a completed reset and
     delivers its code to an inbox an attacker cannot read.

Superusers are **not** exempt. They are the highest-value target, so exempting
them would defeat the control; the two recovery paths above are what make that
safe rather than reckless.
"""

from __future__ import annotations

import logging

from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect
from django.views.decorators.cache import never_cache
from django_ratelimit.exceptions import Ratelimited

from apps.accounts import login_guard

logger = logging.getLogger(__name__)

# Django's AuthenticationForm names the field `username` even when
# USERNAME_FIELD is `email` — the label changes, the POST key does not.
_USERNAME_FIELD = "username"

LOCKED_MESSAGE = (
    "Too many failed sign-in attempts for this account. Reset your password to "
    "regain access, or ask an administrator to run "
    "`manage.py release_login_lock`."
)


@never_cache
def guarded_admin_login(request, extra_context=None):
    """``admin.site.login`` with the API's limits and lock applied.

    Only POSTs are measured. A GET is just the form being rendered, and counting
    it would let a page refresh spend an operator's allowance.
    """
    if request.method != "POST":
        return admin.site.login(request, extra_context)

    email = (request.POST.get(_USERNAME_FIELD) or "").strip().lower()
    tiers = login_guard.admin_login_tiers(email)

    full = login_guard.first_full_tier(request, tiers)
    if full is not None:
        logger.warning(
            "Admin login rate limit exceeded.",
            extra={
                "event": "admin_login_rate_limited",
                "tier": full["group"],
                "retry_after": full["retry_after"],
            },
        )
        # Raised, not rendered: RatelimitMiddleware turns this into the project's
        # standard 429 envelope with a real Retry-After, the same one the API
        # produces. Nothing here is inside DRF, so unlike the API's login view
        # there is no handle_exception to work around.
        exc = Ratelimited()
        exc.retry_after = full["retry_after"]
        raise exc

    user = get_user_model().objects.filter(email=email).first() if email else None

    # Checked BEFORE the password, exactly as the API does: verifying first would
    # leave guessing unbounded and spend ~68ms of PBKDF2 per attempt.
    if login_guard.email_is_locked(email) or (user is not None and user.login_locked()):
        return _refuse_locked(request, email, user)

    response = admin.site.login(request, extra_context)

    # django.contrib.auth.login() sets request.user on success. Checking the
    # user rather than the response status keeps this readable and avoids
    # depending on the admin returning a redirect.
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        login_guard.clear_email_failures(email)
        if user is not None:
            user.reset_failed_logins()
        return response

    login_guard.record_failed_attempt(request, tiers)
    login_guard.record_email_failure(email)
    if user is not None:
        user.record_failed_login()

    if login_guard.email_is_locked(email) or (user is not None and user.login_locked()):
        return _refuse_locked(request, email, user)
    return response


def _refuse_locked(request, email, user):
    """Bounce back to the login form carrying the lockout message.

    A REDIRECT, not a re-render, and the distinction is the whole control.
    ``admin.site.login`` is a view: handed this POST it would authenticate it,
    so re-rendering through it would sign in the very locked account being
    refused — anyone holding the correct password would walk straight past the
    lock. A 302 drops the submitted credentials, and the browser's follow-up is
    a plain GET of the form with nothing left to authenticate.

    ``get_full_path`` rather than a hardcoded path so an ``?next=`` survives the
    bounce; the operator lands back where they were headed once they recover.

    Identical whether or not the address has an account — the email-keyed counter
    in login_guard is what makes that possible, and without it this page would
    answer "does this address have an admin account?" for five wrong passwords.
    The LOG line does distinguish them, because an operator needs to know which
    real account is under attack and an attacker cannot read it.
    """
    logger.warning(
        "Admin login refused: out of attempts.",
        extra={
            "event": "admin_login_account_locked",
            "user_id": str(user.id) if user is not None else None,
            "has_account": user is not None,
            "email_failure_count": login_guard.email_failure_count(email),
        },
    )
    messages.error(request, LOCKED_MESSAGE)
    return HttpResponseRedirect(request.get_full_path())
