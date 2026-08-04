from django.apps import AppConfig


class DocumentHubConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.document_hub'

    def ready(self):
        # Register the post_save signals that auto-seed default documents onto
        # new engagements and the default welcome message onto new portals.
        from . import signals  # noqa: F401
