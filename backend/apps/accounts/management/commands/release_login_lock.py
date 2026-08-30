"""
apps/accounts/management/commands/release_login_lock.py

**The break-glass.** Releases a login lock from the shell, so being locked out of
the Django admin can never mean being unable to get back in.

Why this exists as a command and not just a shell one-liner
-----------------------------------------------------------
The admin's "Release login lock" action is inside the admin — which is the door
you are locked out of. Without an admin-independent path, a lock applied to
``/admin/login/`` would be self-referential: the release button behind the lock.

``manage.py changepassword`` does **not** rescue you either. The lock is
``User.failed_login_count``, which is independent of the password: you would
change it successfully and still be refused.

And a hand-rolled ORM update would only clear half of it. THREE things refuse a
sign-in and they live in two stores:

  1. ``User.failed_login_count`` / ``failed_login_at``   (database)
  2. the email-keyed failure counter                     (cache)
  3. ``auth_login_account`` + ``auth_login_account_daily`` buckets (cache)

Setting the columns to zero looks like it worked and then hands you a 429 on the
next attempt. This calls the same ``login_guard.release_account()`` the admin
action uses, so the two can never drift.

Run it against the PRODUCTION environment, not a laptop pointed at dev: two of
the three counters live in Redis, so a run with the wrong ``CACHE_REDIS_URL``
clears (1) and leaves (2) and (3) holding the lock — the half-release above.

On Render that means a shell on the web service (``render ssh <service>`` or the
dashboard Shell tab). Both are PAID-tier features: while the web service is on
the free plan there is no way to run this in production. Until it moves to a paid
plan, the admin action that calls ``login_guard.release_account()`` is the only
route. Because Postgres (Neon) and the cache (Upstash) are both public TLS
endpoints rather than private-network addresses, a laptop run does work *if* it
is given the production env — which makes pointing at the wrong one the live
hazard here, so check the two URLs before running.

    python manage.py release_login_lock you@example.com
    python manage.py release_login_lock --all        # every locked account
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.accounts import login_guard


class Command(BaseCommand):
    help = "Release a login lock so the account can sign in again. No password change needed."

    def add_arguments(self, parser):
        parser.add_argument("email", nargs="?", help="Account to release.")
        parser.add_argument(
            "--all", action="store_true",
            help="Release every currently locked account. For the both-admins-locked case.",
        )

    def handle(self, *args, **options):
        User = get_user_model()

        if options["all"]:
            # Filtered in Python rather than SQL: login_locked() folds in the
            # ageing rule, and duplicating that as a queryset here is how the two
            # drift. The user table is small enough that this is free.
            targets = [u for u in User.objects.all() if u.login_locked()]
            if not targets:
                self.stdout.write(self.style.SUCCESS("No accounts are locked."))
                return
        else:
            email = (options["email"] or "").strip().lower()
            if not email:
                raise CommandError("Give an email address, or pass --all.")
            user = User.objects.filter(email=email).first()
            if user is None:
                raise CommandError(f"No account with email {email!r}.")
            targets = [user]

        for user in targets:
            was_locked = user.login_locked()
            user.reset_failed_logins()
            freed = login_guard.release_account(user.email)
            self.stdout.write(self.style.SUCCESS(
                f"  released {user.email} "
                f"({'was locked' if was_locked else 'was not locked'}; "
                f"cleared {freed} cache bucket(s))"
            ))

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {len(targets)} account(s) can sign in immediately — "
            "no password change required."
        ))
