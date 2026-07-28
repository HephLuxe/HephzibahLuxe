"""
Project-wide pytest fixtures.

Celery eager mode for the whole test session: several services (e.g.
reminders.services.create_reminder, the document_hub/meetings digest tasks)
call notifications.services.queue_notification(), which does
send_notification_task.delay(...). Without this fixture, .delay() tries to
reach the real Redis broker (config.settings CELERY_BROKER_URL) — if nothing
is listening there (true in this dev environment; no Redis running locally),
the call hangs waiting on a connection that never completes, and the test
process never finishes. This was discovered the hard way: a manual pytest run
against apps/reminders/tests.py hung indefinitely on exactly this path.

task_eager_propagates=True makes a task's exceptions raise synchronously
instead of being swallowed, so a broken task fails the test that triggered it
instead of failing silently.
"""

import pytest


@pytest.fixture(autouse=True, scope="session")
def celery_eager_mode():
    from config.celery import app as celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
