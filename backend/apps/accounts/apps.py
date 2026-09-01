from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.accounts'

    def ready(self):
        # Registers the pre_delete guard that stops a developer account being
        # deleted. Imported here rather than at module scope because signal
        # receivers must not be connected until the app registry is populated —
        # apps/accounts/signals.py resolves settings.AUTH_USER_MODEL as its
        # sender, which needs the model to exist.
        from . import signals  # noqa: F401
