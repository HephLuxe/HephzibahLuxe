# ADR-0001 — Remove Celery; run background work in-process, schedule with platform cron

**Status:** Accepted, implemented
**Date:** 2026-08-26 (P2 addendum 2026-08-28)
**Supersedes:** the DB-backed `django-celery-beat` schedule and the `worker` / `beat` Railway services
**Related:** `RUNBOOK.md`, `docs/OBSERVABILITY_STANDARD.md`, `apps/core/background.py`, `apps/core/timezones.py`

---

## Context

Three always-on Railway services ran from this repo — `web`, `worker`, `beat` — and two of
them existed to service **seven tasks**, of which exactly **one** genuinely needed to be
deferred out of a request (`send_notification_task`, because it makes a Brevo API call) and
one was a delayed job (the event-details debounce). The other five were pure cron: a retry
sweep, a weekly cleanup, two daily digests, and a Brevo health probe.

Every cheap mitigation had already been applied — `CELERY_TASK_IGNORE_RESULT`, remote
control off, task events off, `--without-gossip --without-mingle --without-heartbeat` on the
worker. What none of that could fix:

**Idle Redis command volume.** `broker_transport_options` set `visibility_timeout` but never
`polling_interval`, so kombu's Redis transport blocked on `BRPOP` with a 1-second timeout:
~86,400 commands/day minimum with nothing enqueued, plus `LLEN` and QoS bookkeeping.

**Beat pinned Postgres awake.** `RUNBOOK.md` already documented this: with *every* periodic
task disabled, beat's `DatabaseScheduler` still queried `django_celery_beat_periodictasks`
every ~5 seconds (`DEFAULT_MAX_INTERVAL = 5`) on a persistent connection — enough on its own
to stop Neon scaling to zero. The documented remedy was *"pause / scale the beat service to
0 in Railway"*: a manual workaround for a structural problem.

**Two idle services billed 24/7.** An idle worker and an idle beat still bill RAM and CPU
continuously on Railway.

**The 5-minute Brevo probe.** 288 task publishes/day and 576 outbound HTTPS calls/day
(`api.brevo.com`, and `status.brevo.com` on failure) — the single busiest thing in the system.

## Decision

Remove Celery entirely.

1. **Deferred work runs in a bounded thread pool inside the web process**
   (`apps/core/background.py`). Nothing polls: work is *pushed* into a process that is
   already running, already paid for, and deliberately kept awake.
2. **Scheduling is platform cron.** `manage.py run_scheduled <group>` runs one group of
   tasks to completion and exits. Three cron services replace `worker` + `beat`, and bill
   only for the seconds they actually run.
3. **Redis stays**, doing the job it should have been doing alone all along: the Django
   cache, rate-limit counters, the inquiry double-submit dedupe window and `/health/ready/`.
   `REDIS_URL` (the broker) is gone; `CACHE_REDIS_URL` and its boot guard remain.

### The load-bearing invariant

> **Every deferred task must have a durable status field and a cron sweep that re-drives it.
> A task that can only be triggered once is a task that will be lost.**

This is not advice. The thread pool has no persistence and no delivery guarantee — if the
process dies, in-flight work is gone. That is only safe because the row is written and
committed *first*, and a sweep re-drives anything stranded. A future task that does not
satisfy this is a bug, not a shortcut.

### Cron groups

| Service | Command | Schedule |
|---|---|---|
| `cron-notify` | `run_scheduled notification_retry` | `*/10 * * * *` |
| `cron-daily` | `run_scheduled daily_maintenance` | `0 8 * * *` |
| `cron-weekly` | `run_scheduled weekly_maintenance` | `0 3 * * 1` |

The `*/10` cadence is load-bearing, not a preference — see "Consequences" below.

## What changed, and why each piece is not a mechanical port

### `send_notification_task` lost its in-task retry

`self.retry(countdown=300)` is gone, and no in-thread equivalent replaced it. The reason is
subtler than "no broker": `send_now()` increments `attempt_count` on **every** call, so a
retry loop inside one dispatch would burn all three attempts, and the sweep — which filters
`attempt_count__lt=MAX_ATTEMPTS` — would then skip the row for ever. One dispatch, one
attempt. The cron sweep is the only retry path.

### The retry sweep now also re-drives stranded `QUEUED` rows

The sweep scanned `status=FAILED` only. Consider:

```
queue_notification()  ->  Notification.objects.create(status=QUEUED)   [committed]
                      ->  on_commit -> pool.submit(...)
                      ->  [deploy SIGTERM / OOM / instance restart]
```

That row is `QUEUED` for ever, and nothing looks at it. Under Celery the broker's own
durability mostly plugged this; remove the broker and it becomes the **primary** loss mode.
The sweep now re-drives `QUEUED` rows older than 10 minutes — the age floor matters, or the
sweep races the pool and double-mails a send that is in flight right now.

Note this hole existed *before* the migration too, for any row created while the worker was
down. It was simply much narrower.

### The event-details debounce became a sweep — the one real design change

The debounce was half durable. `EventEngagement.event_details_notify_token` was a column,
but *"and send at T+900s"* lived only in the broker's ETA queue (which is why
`visibility_timeout = 3600` existed), and `what` lived only as a task argument. A worker
restart or a deploy inside the window could drop the email outright, and recovery depended on
redelivery — a Celery implementation detail rather than a guarantee we owned.

Two columns were added (`event_details_notify_due_at`, `event_details_notify_what`) so the
whole schedule is a row, and `apps/events/tasks.dispatch_due_event_details_notifications`
sweeps it. The sweep clears the schedule *first*, filtered on `due_at IS NOT NULL`, so it is
idempotent and a concurrent edit that lands mid-send re-stamps its own later `due_at` — which
is exactly the debounce semantics.

The only regression is precision: a 15-minute debounce swept every 10 minutes lands 15–25
minutes after the last edit rather than exactly 15. For *"the planner finished editing, tell
the client"* that is not a meaningful difference, and in exchange the debounce now survives a
deploy.

### The Brevo health probe was deleted, not ported

288 scheduled runs and 576 outbound HTTPS calls a day, to learn a few minutes earlier what
the next real send would have reported. Detection is now purely passive
(`ServiceHealthState.record_failure` on real send outcomes, threshold 3).

That makes one thing newly essential: a `down` verdict is cleared only by a *successful*
send, and while it is set every normal send parks itself without attempting. With no probe to
break the tie, a stale `down` row would park **every** notification in the platform
indefinitely. `ServiceHealthState.DOWN_STALE_AFTER` (30 min) is the guard: past that,
`is_down()` reports False and the next real send is allowed to probe Brevo itself. The row is
not rewritten, so the admin still shows the last real verdict.

### Both digests: marker committed before the send

`queue_notification()` then `save(reminder_sent_at)` left a window where the email was
already on its way and the marker was still NULL, so the next day's run mailed the client
again. The order is reversed.

Deliberately **not** wrapped in `transaction.atomic()`, which the original plan for this work
prescribed. That reads tidier but inverts the guarantee here: these tasks run in a cron
process, where background dispatch is inline (async is opt-in and only `wsgi.py` opts in), so
the Brevo call would happen *inside* the transaction — before the marker is durable. In
autocommit the marker is committed by the time `queue_notification` is reached, in both
process modes.

### Secrets scrubbed from `Notification.context`

Independent of Celery, and the highest-severity thing the study surfaced.
`Notification.context` is the exact params dict handed to Brevo, and two templates put a live
credential in it (`password_reset` → the 6-digit code, `user_credentials` → the generated
temporary password). Retention made it worse: the weekly cleanup deleted only `SENT` rows
older than 90 days, so a successful credentials email left a plaintext password in Postgres
for 90 days and a failed one left it there **for ever**.

`send_now` now redacts `context` for those templates once a row reaches a terminal state,
`NotificationAdmin` excludes the field, migration `0017` scrubbed what was already stored,
and the weekly cleanup now purges `ABANDONED` as well as `SENT`. A `QUEUED` or `FAILED` row
deliberately keeps its secret — the sweep re-reads it to re-send.

### Reset-code TTL raised 15 → 30 minutes

Consequence of removing in-task retry. The old `self.retry(countdown=300)` gave a second
attempt within 5 minutes; a cron sweep gives one within its cadence. At a 15-minute TTL a
single transient Brevo blip could deliver a code that was already dead — the user gets a code
that cannot work and no explanation. Safe at 30 minutes: a 6-digit code is 10⁶ possibilities
against the existing `10/m` + `50/d` verify limits, i.e. on the order of 300 guesses inside
the window.

### Four maintenance tasks that never ran at all

Each closes a table or bucket that only grew:

- `accounts.flush_expired_jwt_tokens` — `token_blacklist` with `ROTATE_REFRESH_TOKENS` and
  `BLACKLIST_AFTER_ROTATION` both on writes two rows per refresh, ~9 refreshes per user per
  working day. SimpleJWT ships `flushexpiredtokens`; it was wired to nothing.
- `accounts.prune_expired_reset_tokens` — reset tokens were marked used and kept for ever,
  each one a plaintext code plus an IP plus a user.
- `core.clear_expired_sessions` — `clearsessions` was never scheduled.
- `documents.cleanup_orphaned_documents_task` — the existing command documents two real leak
  paths and was scheduled nowhere. At the time of writing, the dev bucket had **140 orphaned
  blobs**, all billed as storage.

## Consequences

### What we gained

- Two always-on services deleted; three cron services that bill execution time only.
- The idle `BRPOP` floor is gone. Redis' remaining traffic is cache and rate limiting.
- Beat no longer holds Postgres awake, so the manual "scale beat to 0" workaround in
  `RUNBOOK.md` is retired.
- Six dependencies dropped (`celery`, `django-celery-beat`, and transitively `kombu`,
  `billiard`, `amqp`, `vine`).
- Four latent correctness bugs fixed that existed **under Celery**: stranded `QUEUED` rows,
  the half-durable debounce, duplicate digest emails, and the permanently-parked breaker.
- Dispatch via `transaction.on_commit`, so a background job can no longer observe a row its
  own transaction has not committed — a race several `queue_notification` callers had
  latently, which Celery's network hop merely tended to lose for us.

### What we lost

| Loss | Mitigation |
|---|---|
| **In-flight work dies on restart** | The durable row plus its cron sweep. This is the central trade, and the invariant above is what makes it safe. |
| **Admin-editable task *timing*** (`PeriodicTask` crontab, no redeploy) | Timing moved to each cron service's schedule in the Railway dashboard — still no redeploy, a different dashboard. The **on/off** switch (`ScheduledTaskSettings`) is untouched and every task still checks it first. |
| **Exact-second scheduling** | Only affected the debounce; now quantised to the sweep cadence, and durable in exchange. |
| **Detecting a Brevo outage before the first failed send** | Accepted. Passive detection at threshold 3, plus the staleness ceiling. |
| **`celery inspect` / Flower** | Already gone (`CELERY_WORKER_ENABLE_REMOTE_CONTROL=False`). The `Notification` admin is the queue view. |
| **Isolated worker memory** | Nothing here builds large in-memory artifacts. `BACKGROUND_MAX_WORKERS` stays at 4. |

### Standing trade to revisit

Each `cron-notify` invocation is a cold Django boot that opens a Postgres connection — 144
boots/day at `*/10`, each waking a suspended Neon compute. Widening to `*/15` or `*/30` is a
real saving, but it is a trade **against password-reset recovery**, because the sweep is the
only retry path and the code lives 30 minutes. Make that change deliberately, and lengthen
`RESET_CODE_TTL_MINUTES` with it.

## Rejected alternatives

Recorded so they are not relitigated.

| Alternative | Why not |
|---|---|
| **Postgres-backed queue with a polling worker** (`django-q2`, `procrastinate`) | Reintroduces the exact Postgres-autosuspend failure that removed beat. We solved this problem once by hand; do not build it back in. |
| **Postgres `LISTEN/NOTIFY`** | Requires a permanently open connection — same autosuspend defeat, and it fights `CONN_MAX_AGE = 0` (pinned in `config/settings.py`). |
| **Keep Celery, move the broker to per-hour-billed Redis** | Genuinely viable, and the honest fallback if in-process execution proves fragile. Rejected because it keeps a worker, a beat, a broker and six dependencies alive to serve **one** real deferral point. |
| **1-minute cron as the only dispatcher** | 1,440 cold boots/day, each waking Postgres, and a full minute of latency on password-reset email. |
| **Upstash QStash / an HTTP push queue** | Technically a good fit — durable, no polling, built-in retries. Rejected on principle: a third-party dependency plus a publicly reachable task endpoint needing signature verification, to solve a problem solvable by *removing* a dependency. |
| **An authenticated `POST /internal/cron/<group>/` endpoint hit by an external scheduler** | Cheaper (no cold boot, runs in the warm web process) but puts a privileged endpoint on the public internet needing constant-time secret comparison and its own rate limit. Platform cron is strictly better here. |
| **APScheduler / an in-process scheduler thread** | Replaces beat's DB poll with a memory-resident timer that dies on every deploy and fires N times with N web instances. |
| **Keeping the Brevo probe on a longer cadence** | Considered. The passive path plus the staleness ceiling covers the failure mode; the probe's only edge was earlier detection, at 576 HTTPS calls/day. |

## Addendum — the P2 follow-ups (2026-08-28)

The study's "P2 — worth doing" list was deferred at first and then applied, with
two exclusions the owner made deliberately: **no production guard on
`RECAPTCHA_SECRET_KEY`**, and **no caching of `/health/ready/`** (nothing probes
it). What landed:

| Item | Decision |
|---|---|
| Reset-code attempt counter | `PasswordResetToken.attempt_count`, five guesses, then the token stops answering. This is what makes the TTL extension above safe, and shipping the TTL without it was the one loose end in the original work. |
| Reset codes hashed at rest | PBKDF2 via `make_password`, ~69 ms per call. A bare digest was rejected as near-useless: 10⁶ six-digit codes enumerate in under a second. Salted, so lookup-by-code is gone — verification fetches the user's one outstanding token, which the "invalidate prior tokens" rule guarantees. |
| `DEFERRED` notification status | Added, plus a data migration relabelling the rows that already meant it. Behaviour unchanged (`tasks.RETRYABLE_STATUSES` covers both), so this is purely about the admin not calling un-attempted mail a failure. |
| Blocking lint + coverage floor | `ruff check` is now blocking and `--cov-fail-under=77` is pinned at the coverage of this commit. **`ruff format` was deliberately not run** — 104 files, and this codebase's hand-alignment is intentional; CI gates on `ruff check`, which is what catches real problems. |
| Cached Brevo SDK client | Built once behind a lock, `connection_pool_maxsize` pinned to `BACKGROUND_MAX_WORKERS + 4`. Safe to share: the transport is a thread-safe `urllib3.PoolManager`, and `ApiClient`'s lazy `ThreadPool` only serves `async_req=True`, which is never used here. |
| Inquiry fan-out (`BACKGROUND_MAX_WORKERS`) | **Left at 4 on the owner's instruction.** The fan-out itself is sound and unchanged; the consequence — a 10-staff fan-out under load runs some sends inline in the request thread — is accepted and documented rather than papered over with more threads. |
| Digest timezone handling | See below. |

### The timezone decision

`TIME_ZONE` stays `UTC` and `USE_TZ` stays `True` — that is how instants should be
stored. The bug was elsewhere: `PaymentMilestone.due_date` and `Meeting.date` are
naive `DateField`s meaning *a calendar day where the client lives*, and the digests
compared them against `timezone.now().date()`, which is UTC's day. At 23:30 UTC a
client in Auckland is already on tomorrow and one in Midway is still on today, so a
three-day lookahead fires two or four days out depending on where they are.

**Chosen: resolve the calendar day per recipient** (`User.timezone`, falling back
to `settings.PLATFORM_DEFAULT_TIMEZONE`, `apps/core/timezones.py`). The brief is a
platform used worldwide, and a single business timezone — the simpler option, and
the right one for a practice serving one country — only moves the off-by-one from
"anyone not in UTC" to "anyone not in Lagos".

Query shape worth recording, because the obvious implementation is worse: widen the
SQL window by one day (the maximum possible skew — real offsets span UTC-12 to
UTC+14, so a local date is never two days out) to get a guaranteed superset, then
make the exact per-recipient decision in Python. One query and an exact answer,
versus one query per distinct timezone.

Reads fail soft: an unknown zone name logs `event=unknown_timezone` and degrades to
UTC, because one malformed row must not take out a whole digest run. Writes fail
loud (`User.clean()`, the API's `TimezoneField`), or the soft fallback would hide
the mistake indefinitely.

**Follow-up, deliberately not done here:** this fixes *which day* the digest
measures, not *when it fires*. That is still one cron run at 08:00 UTC, so an
Auckland client reads it at 21:00 and a Los Angeles client at 01:00. Fixing that
means running the sweep hourly and sending to each timezone at its own local 08:00
— a real improvement, a different change, and one that multiplies the cron cadence
(and therefore the Neon wake-ups traded against in "Standing trade to revisit"
above). Worth doing when digest delivery time becomes a complaint rather than a
theory.

## Measurements

**Not yet taken on this deployment.** §1.5's figures above are reasoned from kombu's
transport behaviour and from `RUNBOOK.md`'s own account of beat holding Postgres awake — they
are not instrumented numbers from this Railway project. Record real before/after figures
here:

1. **Idle Redis command volume** — `redis.config_resetstat()`, sleep 300s,
   `info("commandstats")`, sum `calls`. If your Redis is billed per RAM-hour rather than per
   command, this number is informational and the *service* cost is the driver.
2. **What holds Postgres awake** — the `pg_stat_activity` query in `RUNBOOK.md`, run during a
   quiet hour.
3. **Railway per-service cost** — 30-day usage for `web` / `worker` / `beat` before, and
   `web` plus the three cron services after.

## Rollback

Revert the commit and recreate the `worker` + `beat` services. **Suspend rather than delete
those two Railway services for two weeks**, so rollback is a dashboard toggle rather than a
blueprint rebuild.

Two things do not roll back cleanly and are worth knowing:

- Migration `0017` is irreversible by design — the scrubbed plaintext is not recoverable.
- `portal.0015` adds two nullable columns; reverting the code without reverting the migration
  is harmless (the columns are simply unread).

The `django_celery_beat` tables are dropped by `migrate django_celery_beat zero`, which must
be run **before** the app is removed from `INSTALLED_APPS` or the tables linger. No migration
in `apps/*/migrations/` references the app, so it could be removed outright — verified by a
full fresh-database test run.
