from django.apps import AppConfig


class MeetingsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.meetings'

    def ready(self):
        # Register the post_delete signal that keeps the Document registry and
        # file storage in step when a prep upload is deleted (see signals.py).
        from . import signals  # noqa: F401
