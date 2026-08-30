from django.apps import AppConfig


class PortalConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.portal'

    def ready(self):
        # Imported for its SIDE EFFECTS, not for a name: apps/portal/signals.py
        # registers the post_save receiver that auto-creates a ClientPortal when a
        # user with role=client is saved. Without this line no portal is ever
        # created and most of the platform has nothing to hang off.
        #
        # The noqa is load-bearing. `ruff check --fix` deleted this import as
        # unused and replaced the body with `pass`, which broke portal creation
        # silently — the app still booted, endpoints still answered, clients just
        # had no portal. Do not "clean this up".
        import apps.portal.signals  # noqa: F401
