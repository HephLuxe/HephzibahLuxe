"""
apps/core/background.py

In-process background execution — the replacement for the Celery broker.
See docs/adr/0001-remove-celery.md.

Why this exists
---------------
This deployment paid for a Celery worker and a Celery beat service, always on,
to service seven tasks — of which exactly one genuinely needed to be deferred
out of the request path. What that bought instead:

  * An idle ``BRPOP`` floor. kombu's Redis transport blocks with a 1s timeout,
    so a completely idle worker still issues ~86,400 commands/day, plus ``LLEN``
    and QoS bookkeeping, with nothing enqueued.
  * beat's ``DatabaseScheduler`` polling ``django_celery_beat_periodictasks``
    every ~5s on a persistent connection — enough on its own to stop a
    serverless Postgres from ever scaling to zero (see RUNBOOK.md).

Both stores punish polling, so nothing here polls. Work is *pushed* into a
bounded thread pool inside the web process — which is already running, already
paid for, and deliberately kept awake.

The durability model
--------------------
This module is deliberately not a queue. It has no persistence, no broker and no
delivery guarantee: if the process dies, in-flight work is gone. That is
acceptable only because every deferred task in this codebase writes a durable
row *first* (``Notification.status``, ``EventEngagement.event_details_notify_due_at``)
and a platform cron sweep (``manage.py run_scheduled``) re-drives anything left
stranded.

    Rule: a task with no status field and no sweep will eventually be lost.
    Do not add one.

Process modes
-------------
Async is opt-in, and only ``config/wsgi.py`` opts in. Everywhere else —
management commands, all three cron groups, ``shell``, tests, data migrations —
``.delay()`` runs the work **inline**.

This is not a nicety. ``retry_failed_notifications_task`` calls ``.delay()``
from inside a cron process that exits the moment the command returns. Submitting
to a thread pool there would hand work to a pool that is destroyed seconds
later, so the sweep would silently drop exactly the mail it exists to rescue.
Inline-by-default means a cron sweep sends its own mail before exiting, with no
special-casing at the call sites.

Usage
-----
    @background_task(name="notifications.send")
    def send_notification_task(notification_id, force=False): ...

    send_notification_task.delay(id)   # async in web, inline everywhere else
    send_notification_task.apply()     # always synchronous; never raises
    send_notification_task(id)         # always synchronous; raises

    # One-shot, later, in the web process only. NO-OP elsewhere (not inline —
    # a delayed task run inline would block a cron run for the whole delay).
    # Opportunistic precision on top of a durable row; the sweep is the promise.
    sweep_task.schedule_in(900, key=f"event_details:{engagement.pk}")

There is deliberately no tracked/pollable job variant: nothing in this codebase
exposes a ``GET /tasks/<id>/`` endpoint and nothing ever called ``.get()`` or
``AsyncResult`` (consistent with the old ``CELERY_TASK_IGNORE_RESULT = True``),
so a ``BackgroundJob`` row would be a write per dispatch that no one reads.
"""

from __future__ import annotations

import contextvars
import functools
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import connections, transaction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Process mode
# ---------------------------------------------------------------------------

_async_enabled = False


def enable_async() -> None:
    """
    Declare this process long-lived enough to finish background work.

    Called from ``config/wsgi.py`` and nowhere else. A process that has not
    called this runs every ``.delay()`` inline, so short-lived processes cannot
    hand work to a pool that dies with them.
    """
    global _async_enabled
    _async_enabled = True
    logger.info(
        "background: async dispatch enabled (max_workers=%s, max_queued=%s)",
        _max_workers(),
        _max_queued(),
    )


def disable_async() -> None:
    """Undo :func:`enable_async`. For tests only."""
    global _async_enabled
    _async_enabled = False


def async_enabled() -> bool:
    """True when ``.delay()`` dispatches to the pool rather than running inline."""
    if getattr(settings, "BACKGROUND_EAGER", False):
        # Test/debug override: force inline even inside the web process.
        return False
    return _async_enabled


def _max_workers() -> int:
    return getattr(settings, "BACKGROUND_MAX_WORKERS", 4)


def _max_queued() -> int:
    return getattr(settings, "BACKGROUND_MAX_QUEUED", 100)


def _max_timers() -> int:
    return getattr(settings, "BACKGROUND_MAX_TIMERS", 50)


def armed_timers() -> int:
    """Number of timers currently waiting. Exposed for tests and diagnostics."""
    with _timers_lock:
        return len(_timers)


def cancel_timers() -> None:
    """Cancel every armed timer. For tests, and for a clean shutdown."""
    with _timers_lock:
        for timer in _timers.values():
            timer.cancel()
        _timers.clear()


# ---------------------------------------------------------------------------
# The pool
# ---------------------------------------------------------------------------
# Created lazily so importing this module never spawns threads — matters for
# management commands, migrations and the test suite, none of which dispatch.

_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
_inflight = 0
_inflight_lock = threading.Lock()

# Armed one-shot timers, keyed by caller-supplied string so a second arming for
# the same subject replaces the first instead of stacking. Without that, twenty
# edits inside one debounce window would leave twenty threads asleep, nineteen of
# which wake only to find the row already claimed.
_timers: "dict[str, threading.Timer]" = {}
_timers_lock = threading.Lock()


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadPoolExecutor(
                    max_workers=_max_workers(), thread_name_prefix="bg"
                )
    return _pool


def shutdown(wait: bool = True) -> None:
    """
    Stop the pool. Mainly for tests — at interpreter exit,
    ThreadPoolExecutor's own atexit hook already joins its threads, which is
    what gives in-flight work a chance to finish during a deploy's SIGTERM
    grace period.
    """
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown(wait=wait)
            _pool = None


def inflight() -> int:
    """Number of jobs queued or running. Exposed for tests and diagnostics."""
    return _inflight


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class TaskResult:
    """
    Return value of ``.apply()``. Mirrors the slice of Celery's EagerResult that
    ``run_scheduled`` depends on: it never raises, so one failing task in a group
    does not strand the rest.
    """

    __slots__ = ("_value", "_exc")

    def __init__(self, value=None, exc: BaseException | None = None):
        self._value = value
        self._exc = exc

    def successful(self) -> bool:
        return self._exc is None

    def failed(self) -> bool:
        return self._exc is not None

    @property
    def result(self):
        """The return value, or the exception instance when the task failed."""
        return self._exc if self._exc is not None else self._value

    def get(self, propagate: bool = True):
        """
        Return value, raising the task's exception when it failed.

        Mirrors Celery's ``EagerResult.get`` so a call site reads the same
        either side of the migration — ``.result`` hands back the exception
        object, ``.get()`` raises it.
        """
        if self._exc is not None and propagate:
            raise self._exc
        return self._value

    def __repr__(self) -> str:
        state = "SUCCESS" if self.successful() else "FAILURE"
        return f"<TaskResult {state} {self.result!r}>"


class TaskHandle:
    """
    Return value of ``.delay()``. Carries an ``.id`` purely as a correlation id
    for log tracing — nothing in this codebase polls a task by id.
    """

    __slots__ = ("id",)

    def __init__(self, job_id):
        self.id = str(job_id)

    def __repr__(self) -> str:
        return f"<TaskHandle {self.id}>"


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


class BackgroundTask:
    """Wraps a plain function with dispatch helpers. Created by @background_task."""

    def __init__(self, fn, name: str):
        self.fn = fn
        self.name = name
        functools.update_wrapper(self, fn)

    # -- synchronous ------------------------------------------------------

    def __call__(self, *args, **kwargs):
        """Run inline and propagate exceptions. Plain function semantics."""
        return self.fn(*args, **kwargs)

    def run(self, *args, **kwargs):
        """Alias for __call__, for call sites where ``task.run(...)`` reads better."""
        return self.fn(*args, **kwargs)

    def apply(self, args=None, kwargs=None) -> TaskResult:
        """
        Run inline and capture the outcome instead of raising.

        ``run_scheduled`` relies on this: it runs a whole group and reports every
        failure at the end rather than aborting on the first one.
        """
        try:
            return TaskResult(value=self.fn(*(args or ()), **(kwargs or {})))
        except Exception as exc:  # noqa: BLE001 - deliberately broad, mirrors Celery
            logger.exception("background task failed", extra={"task": self.name})
            return TaskResult(exc=exc)

    # -- dispatch ---------------------------------------------------------

    def delay(self, *args, **kwargs) -> TaskHandle:
        """
        Dispatch. Async in the web process, inline everywhere else.

        Async dispatch goes through ``transaction.on_commit``, so a task can
        never observe a row its own transaction has not committed yet. That race
        existed latently under Celery too — ``queue_notification`` dispatched
        inline while several of its callers ran inside ``transaction.atomic()``
        (portal, document_hub, accounts services) — but the broker's network hop
        usually lost it. A thread will not.

        Inline dispatch runs immediately rather than on commit, because under
        ``TestCase`` (and pytest-django's ``db`` fixture) the enclosing
        transaction is rolled back and on-commit callbacks never fire. Running
        immediately preserves the semantics the existing suite was written
        against with ``CELERY_TASK_ALWAYS_EAGER``.
        """
        handle = TaskHandle(uuid.uuid4())

        if not async_enabled():
            self._execute(args, kwargs)
            return handle

        if not self._reserve_slot():
            # Backpressure: degrade to inline rather than drop work or grow the
            # queue without bound. Slower, never lossy. This is a real path
            # here: apps/inquiries/services.py fans out one notification per
            # flagged staff member on a public, unauthenticated endpoint.
            logger.error(
                "background: queue full (%s in flight), running %s inline",
                _inflight,
                self.name,
                extra={"task": self.name},
            )
            self._execute(args, kwargs)
            return handle

        # Carry the caller's context (notably the X-Request-ID contextvar set by
        # RequestIDMiddleware) into the worker thread. ThreadPoolExecutor does
        # not do this for you, so without it every background log line would
        # lose the correlation id that ties it back to the request that caused
        # it — which is what the Celery signal handlers in
        # apps/core/observability existed to preserve across the broker.
        ctx = contextvars.copy_context()

        def _submit():
            try:
                _get_pool().submit(ctx.run, self._execute_pooled, args, kwargs)
            except Exception:
                _release_slot()
                logger.exception(
                    "background: submit failed for %s",
                    self.name,
                    extra={"task": self.name},
                )
                raise

        transaction.on_commit(_submit)
        return handle

    def schedule_in(self, delay_seconds: float, *args, key: str, **kwargs) -> bool:
        """
        Run this task once, ``delay_seconds`` from now, in the web process.

        **Opportunistic only — never the guarantee.** The caller must already
        have written a durable row that a cron sweep will pick up regardless
        (module docstring: *a task with no status field and no sweep will
        eventually be lost*). All this buys is punctuality: the sweep would have
        found the same work at its next run, up to a whole cron interval later.
        A deploy, a worker recycle or a crash simply drops the timer and the
        sweep does its job.

        Three deliberate differences from ``.delay()``:

        * **Outside the web process this is a NO-OP, not inline.** ``.delay()``
          degrades to running immediately, which is right for work that must
          happen now. Running a *delayed* task inline would block a management
          command or a cron run for the whole delay — so short-lived processes
          decline to arm anything and leave it to the sweep.
        * **Keyed, and re-arming replaces.** A debounce re-stamped on every edit
          would otherwise leave one sleeping thread per edit.
        * **Bounded.** Past ``BACKGROUND_MAX_TIMERS`` nothing is armed. Refusing
          to arm costs precision, never correctness, which is exactly the
          property that makes a hard cap safe here.

        Returns True when a timer was armed.

        Not routed through ``transaction.on_commit``: if the caller's
        transaction rolls back, the row it would have acted on is gone and the
        task's own ``due_at <= now`` filter matches nothing. A wasted wake-up,
        not a wrong one — and arming directly keeps this callable from a test.
        """
        if not async_enabled():
            return False

        with _timers_lock:
            existing = _timers.pop(key, None)
            if existing is not None:
                existing.cancel()
            if len(_timers) >= _max_timers():
                logger.warning(
                    "background: timer cap reached (%s), not arming %s — the cron "
                    "sweep will still deliver it, just later",
                    _max_timers(),
                    self.name,
                    extra={"event": "background_timer_cap", "task": self.name},
                )
                return False

            # Carry the request's correlation id into the timer thread, same as
            # the pool does — otherwise the eventual log line cannot be traced
            # back to the edit that scheduled it.
            ctx = contextvars.copy_context()
            timer = threading.Timer(
                max(0.0, delay_seconds),
                self._fire_timer,
                args=(key, args, kwargs, ctx),
            )
            timer.daemon = True  # never hold up interpreter shutdown
            timer.name = f"bg-timer-{self.name}"
            _timers[key] = timer
            timer.start()
        return True

    def _fire_timer(self, key, args, kwargs, ctx) -> None:
        with _timers_lock:
            _timers.pop(key, None)
        try:
            ctx.run(self._execute, args, kwargs)
        finally:
            # Same contract as _execute_pooled: a leaked thread-local connection
            # keeps a serverless Postgres awake, which is the bill this whole
            # module exists to avoid.
            connections.close_all()

    # -- execution --------------------------------------------------------

    def _execute_pooled(self, args, kwargs) -> None:
        try:
            self._execute(args, kwargs)
        finally:
            # Thread-local DB connections MUST be closed here. Django holds one
            # connection per thread, and a leaked one keeps a serverless
            # Postgres awake — trading the Redis bill for the Neon bill that
            # CONN_MAX_AGE=0 and removing beat exist to avoid. (CONN_MAX_AGE
            # cannot help here: it is applied by request_finished, which a
            # background thread never fires. This finally block IS the mechanism
            # for pool work.)
            connections.close_all()
            # Released last, after every cleanup: inflight() is what backpressure
            # and tests read to mean "fully done". Releasing before the
            # connection is closed would let a caller observe an idle pool while
            # a thread still holds a database connection open.
            _release_slot()

    def _execute(self, args, kwargs) -> None:
        """
        Run the task body, swallowing exceptions after logging them.

        Broad on purpose: a thread that raises disappears without a trace, which
        is strictly worse than Celery, which at least logged. The log line plus
        Sentry's logging integration is what makes a failed background task
        visible at all.
        """
        try:
            self.fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            logger.exception("background task failed", extra={"task": self.name})

    def _reserve_slot(self) -> bool:
        global _inflight
        with _inflight_lock:
            if _inflight >= _max_queued():
                return False
            _inflight += 1
            return True

    def __repr__(self) -> str:
        return f"<BackgroundTask {self.name}>"


def _release_slot() -> None:
    global _inflight
    with _inflight_lock:
        _inflight = max(0, _inflight - 1)


def background_task(name: str | None = None):
    """
    Declare a function as deferrable.

    ``name`` is a stable identifier used in logs. The Celery registered names
    are kept verbatim ("notifications.send", "notifications.retry_failed", ...)
    so log history stays greppable across the migration.
    """

    def decorator(fn) -> BackgroundTask:
        return BackgroundTask(fn, name or f"{fn.__module__}.{fn.__name__}")

    return decorator
