from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'

    def ready(self):
        # Import for its side effects: connects the Celery request-id
        # propagation signal handlers (before_task_publish / task_prerun /
        # task_postrun). This must happen regardless of whether Sentry's DSN is
        # configured, so correlation IDs flow into workers even with Sentry off.
        from apps.core import observability  # noqa: F401
