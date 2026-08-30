"""
Project-wide pytest configuration.

Deliberately empty of fixtures. It used to hold a session-scoped
``celery_eager_mode`` fixture, because several services
(reminders.services.create_reminder, the document_hub/meetings digest tasks)
queue a notification as a side effect, and without eager mode ``.delay()``
tried to reach a real Redis broker — hanging indefinitely when nothing was
listening, which is exactly what a dev machine with no Redis looks like.

There is no broker any more. Deferred work runs in-process
(apps/core/background), and two independent things make it synchronous here:

  * ``settings.BACKGROUND_EAGER`` is forced True under the test runner, and
  * async dispatch is opt-in per process — only ``config/wsgi.py`` calls
    ``background.enable_async()``, so pytest's process never opts in at all.

Either one alone would be enough; both hold, so a test asserting on the
Notification row a service produced sees it already written.
"""
