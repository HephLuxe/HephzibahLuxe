"""
apps/core/tasks.py

Project-wide housekeeping with no better home. Runs from cron group
`daily_maintenance` (apps/core/management/commands/run_scheduled.py).
"""

import logging

from django.core.management import call_command

from apps.core.background import background_task

logger = logging.getLogger(__name__)


@background_task(name="core.clear_expired_sessions")
def clear_expired_sessions() -> None:
    """
    Delete expired rows from `django_session`.

    `django.contrib.sessions` uses the database backend and Django's
    `clearsessions` was never scheduled. Growth is slow here — the API is JWT and
    only the Django admin uses sessions at all — but it is monotonic, and
    Postgres never reclaims a table nobody deletes from.
    """
    from apps.notifications.models import ScheduledTaskSettings

    if not ScheduledTaskSettings.is_task_enabled("core_clear_sessions"):
        return

    call_command("clearsessions")
