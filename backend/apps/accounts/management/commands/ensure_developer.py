"""
apps/accounts/management/commands/ensure_developer.py

Creates or repairs the developer accounts named in
``settings.PLATFORM_DEVELOPER_EMAILS``. Idempotent, so it is safe to run on
every deploy — and it is meant to be, from the release phase:

    web: python manage.py migrate --noinput \\
         && python manage.py ensure_developer \\
         && python manage.py collectstatic --noinput \\
         && gunicorn ...

Why a command rather than a data migration
------------------------------------------
A migration runs once, against whatever the environment was at that moment, and
is then frozen in the history. The developer list is deployment config that can
change — a co-founder added, a laptop-only address dropped — and a migration
cannot react to that. It would also bake an email address into version control,
which is the one place this design is trying to keep it out of.

Running every deploy is the point: it is the mechanism that makes "the developer
account cannot be removed" true across a database restore, a fresh environment,
or a manual delete that somehow got past the ``pre_delete`` guard. The account
comes back at the next deploy.

Passwords
---------
A new account gets a random password and ``force_password_change``, exactly like
a staff-registered user, and you set the real one through the password-reset
flow. ``--password`` is offered for bootstrapping an environment with no working
outbound mail (a fresh dev database), and it prints a warning because a password
on a command line lands in shell history and in the deploy log.

An EXISTING account's password is never touched. That matters: this command runs
on every deploy, and a version that reset the password each time would undo the
developer's own password on every push.
"""

import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts import developers
from apps.accounts.models import UserRole


class Command(BaseCommand):
    help = (
        "Create or repair the developer accounts listed in "
        "PLATFORM_DEVELOPER_EMAILS. Idempotent — safe on every deploy."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--first-name", default="Platform",
            help="First name for accounts this run CREATES. Ignored for existing ones.",
        )
        parser.add_argument(
            "--last-name", default="Developer",
            help="Last name for accounts this run CREATES. Ignored for existing ones.",
        )
        parser.add_argument(
            "--password", default=None,
            help=(
                "Set this password on accounts this run CREATES, instead of a "
                "random one recovered by password reset. For bootstrapping an "
                "environment with no outbound mail. Never applied to an "
                "existing account."
            ),
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        emails = sorted(developers.developer_emails())
        if not emails:
            # Not an error. A CI run, a reviewer's checkout and a staging box
            # with no developer account are all legitimate, and failing the
            # deploy over an unset optional variable would be the command
            # causing the outage it exists to prevent.
            self.stdout.write(
                "PLATFORM_DEVELOPER_EMAILS is empty — no developer accounts to "
                "ensure. Set it in the deployment environment (comma-separated) "
                "to protect an account."
            )
            return

        dry_run = options["dry_run"]
        if options["password"]:
            self.stdout.write(self.style.WARNING(
                "--password was supplied. It is now in this shell's history and "
                "in the deploy log. Change it after first sign-in."
            ))

        User = get_user_model()
        created = repaired = unchanged = 0

        for email in emails:
            user = User.objects.filter(email__iexact=email).first()

            if user is None:
                if dry_run:
                    self.stdout.write(f"  would CREATE  {email}")
                    created += 1
                    continue
                user = self._create(User, email, options)
                created += 1
                self.stdout.write(self.style.SUCCESS(f"  created   {email}"))
                if not options["password"]:
                    self.stdout.write(
                        "            Sign in via the password-reset flow — the "
                        "temporary password was random and is not recoverable."
                    )
                continue

            # Existing account: re-derive the four privilege fields. This is the
            # path that undoes a demotion, and the reason the command is worth
            # running on a deploy where nothing was created.
            drift = developers.apply_state(user)
            if not drift:
                unchanged += 1
                self.stdout.write(f"  ok        {email}")
                continue

            if dry_run:
                self.stdout.write(f"  would FIX     {email} ({', '.join(drift)})")
            else:
                user.save(update_fields=drift)
                self.stdout.write(self.style.WARNING(
                    f"  repaired  {email} — corrected {', '.join(drift)}"
                ))
            repaired += 1

        summary = (
            f"{created} created, {repaired} repaired, {unchanged} already correct."
        )
        self.stdout.write(self.style.SUCCESS(
            ("DRY RUN — nothing written. " if dry_run else "") + summary
        ))

    @transaction.atomic
    def _create(self, User, email, options):
        password = options["password"] or secrets.token_urlsafe(24)
        user = User.objects.create_user(
            first_name=options["first_name"],
            last_name=options["last_name"],
            email=email,
            password=password,
            role=UserRole.DEVELOPER,
        )
        # Only when the password was generated: a bootstrap password supplied on
        # purpose should not be immediately invalidated by a forced change the
        # operator did not ask for.
        if not options["password"]:
            user.force_password_change = True
            user.save(update_fields=["force_password_change"])
        return user
