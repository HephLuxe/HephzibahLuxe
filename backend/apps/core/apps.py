from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'

    # No ready() hook. This used to import apps.core.observability for its side
    # effects — connecting the Celery before_task_publish / task_prerun /
    # task_postrun handlers that carried the X-Request-ID correlation id across
    # the broker. There is no broker: apps/core/background copies the caller's
    # contextvars straight into the worker thread, so nothing needs connecting.
    # Sentry itself is still initialised from config/settings.py, guarded by
    # SENTRY_DSN.
