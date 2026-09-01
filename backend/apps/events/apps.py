from django.apps import AppConfig


class EventsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.events'

    def ready(self):
        # Registers the post_delete receiver that removes a gallery image's blob
        # from storage. Importing here (not at module scope) is the standard
        # placement — signals.py imports models, which aren't loaded yet when
        # this module is first read.
        from . import signals  # noqa: F401
