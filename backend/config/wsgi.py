"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()

# This is the ONE process that opts into asynchronous background dispatch
# (apps/core/background). Everything else — management commands, the three
# `run_scheduled` cron groups, `shell`, tests, data migrations — runs every
# `.delay()` inline, because a process that exits in seconds must not hand work
# to a thread pool that dies with it. `retry_failed_notifications_task` is the
# reason: it dispatches sends from inside a cron process, and a pool there would
# silently drop exactly the mail the sweep exists to rescue.
#
# Called AFTER get_wsgi_application() so the app registry and settings are fully
# loaded before any thread can start touching models.
from apps.core import background  # noqa: E402

background.enable_async()
