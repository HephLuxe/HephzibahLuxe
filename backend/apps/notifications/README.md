# notifications

Delivery record + dispatch machinery for every email the platform sends.
Every app that wants to email a client goes through
`notifications.services.queue_notification()` rather than calling Brevo's
API directly — that's what makes retries, a failure audit trail (queryable
in admin), and a cleanup sweep possible from one place instead of N ad-hoc
copies scattered across apps.

This is mostly backend plumbing: `Notification` rows are an email audit trail,
primarily for admin/support use, and the **actionable** in-portal surface is the
`reminders` app, not this one. The only API here is a **read-only history**
(`GET /notifications/` — see "In-portal history" below); nothing outside
`services.queue_notification()` ever creates a notification.

## Delivery: Brevo's transactional email API

All outbound mail sends through [Brevo](https://www.brevo.com)'s
transactional email API (`sib-api-v3-sdk`), not Django's SMTP backend.
Every `NotificationType` maps 1:1 to a real template built in the Brevo
dashboard — there is no local HTML rendering or fallback path; a
`template_name` with no configured Brevo template ID is a configuration
error (`send_now` fails loudly, `Notification.status = FAILED` with a clear
`error_message`), not a graceful-degradation case.

- **`BREVO_API_KEY`** (`config/settings.py`, required — raises
  `ImproperlyConfigured` at boot if blank, same pattern as
  `FRONTEND_BASE_URL`).
- **One `BREVO_TEMPLATE_<NAME>` setting per `NotificationType`** — an int,
  the numeric ID Brevo assigns the template in its dashboard.
  `services.TEMPLATE_ID_MAP` maps each `NotificationType` value to its
  settings attribute name.
- `queue_notification(...)`'s `context` dict becomes Brevo's template
  `params` directly — referenced in the Brevo editor as `{{ params.title }}`,
  `{{ params.amount }}`, etc. The **real subject line lives in the Brevo
  template**, not in code — `Notification.subject` only stores the
  `NotificationType` label (e.g. "New reminder") for the admin audit-trail
  column.
- `services._serialise_context()` converts `Decimal`/`UUID`/`date`/
  `datetime` values in `context` to JSON-safe primitives automatically
  (both for the `Notification.context` JSONField and for Brevo's `params`)
  — callers no longer need to hand-cast `str(...)` at each call site.

## Model — `Notification`
`recipient_email`, `recipient_user` (nullable FK), `engagement` (nullable FK —
most notifications are engagement-scoped, but the field stays optional so a
future staff-facing alert isn't forced to fake one), `template_name`,
`subject`, `context` (JSONField — Brevo template params), `status`
(queued/sent/failed/**deferred**/**abandoned** — see "Retry policy" below), `sent_at`,
`error_message`, `attempt_count`.

## Model — `NotificationTypeSettings`
Per-`template_name` on/off switch, configured in the Django admin (no API) —
e.g. turn off "payment due" reminders without silencing everything else. One
row per `NotificationType`; `NotificationTypeSettings.is_enabled(template_name)`
is the check `queue_notification` applies. **A template with no row is
treated as enabled** (fail-open) — so a newly added `NotificationType` works
immediately, before anyone remembers to add a settings row for it. A data
migration seeds a row (enabled) for every type that exists today.

`retry_failed_notifications_task` (the recovery sweep) applies this same
per-type check itself, since it re-drives existing rows directly rather than
going through `queue_notification`.

## Model — `ScheduledTaskSettings`
A **different** on/off switch, orthogonal to `NotificationTypeSettings`:
whether a background/scheduled task runs **at all**, independent of
whether any individual notification it might produce is itself enabled. Same
admin-only, fail-open shape (`is_task_enabled(task_key)` — no row means the
task runs normally). Gates the digest/sweep tasks, *not* the low-level
per-notification sender (`send_notification_task`) — see the reasoning in
that task's own docstring for why it's deliberately excluded (gating it
would strand already-approved, already-queued notifications rather than
stop anything meaningful).

| `task_key` | Task | Cron group |
|---|---|---|
| `notifications_retry_failed` | `notifications.tasks.retry_failed_notifications_task` | `notification_retry` (`*/10`) |
| `event_details_notification` | `events.tasks.dispatch_due_event_details_notifications` | `notification_retry` (`*/10`) — see `apps/events/README.md` |
| `payment_due_digest` | `document_hub.tasks.payment_due_digest_task` | `daily_maintenance` (08:00 UTC) |
| `meeting_prep_digest` | `meetings.tasks.meeting_prep_digest_task` | `daily_maintenance` |
| `accounts_prune_reset_tokens` | `accounts.tasks.prune_expired_reset_tokens` | `daily_maintenance` |
| `accounts_flush_expired_jwt` | `accounts.tasks.flush_expired_jwt_tokens` | `daily_maintenance` |
| `core_clear_sessions` | `core.tasks.clear_expired_sessions` | `daily_maintenance` |
| `notifications_cleanup_old` | `notifications.tasks.cleanup_old_notifications_task` | `weekly_maintenance` (Mon 03:00 UTC) |
| `documents_cleanup_orphaned` | `documents.tasks.cleanup_orphaned_documents_task` | `weekly_maintenance` |

Group membership lives in `apps/core/management/commands/run_scheduled.py`;
`python manage.py run_scheduled --list` prints it.

For `event_details_notification`, the gate is checked in **two** places:
`events.services.schedule_event_details_notification` doesn't stamp a due time
when disabled, plus a defensive check inside the sweep for a row that was
stamped before the switch was flipped off.

`notifications_brevo_health_probe` used to be here. The probe was removed with
Celery — 288 scheduled runs and 576 outbound HTTPS calls a day, to learn a few
minutes earlier what the next real send would report — and migration
`0016_scheduled_task_keys_for_cron` deletes its row.

## Templates to build in Brevo

Each row's params are exactly the `context` dict its caller builds — build
a Brevo template expecting these merge fields, then set its ID in
`BREVO_TEMPLATE_<NAME>`.

| `template_name` | Settings var | Fired by | Params |
|---|---|---|---|
| `new_reminder` | `BREVO_TEMPLATE_NEW_REMINDER` | `reminders.services.create_reminder` (immediate) | `title`, `description`, `priority_display`, `due_date`, `link_url`, `link_label` |
| `payment_due` | `BREVO_TEMPLATE_PAYMENT_DUE` | `document_hub.tasks.payment_due_digest_task` (daily cron) | `label`, `amount`, `due_date`, `event_title` |
| `meeting_prep_due` | `BREVO_TEMPLATE_MEETING_PREP_DUE` | `meetings.tasks.meeting_prep_digest_task` (daily cron) | `meeting_title`, `meeting_date`, `incomplete_count` |
| `phase_advanced` | `BREVO_TEMPLATE_PHASE_ADVANCED` | `portal.services.set_phase` / `advance_phase` (immediate) | `phase_display`, `event_title` |
| `event_details_updated` | `BREVO_TEMPLATE_EVENT_DETAILS_UPDATED` | `events.services.schedule_event_details_notification` (**debounced**) | `event_title`, `what` |
| `document_added` | `BREVO_TEMPLATE_DOCUMENT_ADDED` | `document_hub.views.create_document` (immediate) | `document_title`, `category_display`, `event_title` |
| `invoice_issued` | `BREVO_TEMPLATE_INVOICE_ISSUED` | `document_hub.views.create_invoice` (immediate) | `invoice_number`, `amount`, `due_on`, `event_title` |
| `receipt_issued` | `BREVO_TEMPLATE_RECEIPT_ISSUED` | `document_hub.views.create_receipt` (immediate) | `receipt_number`, `amount`, `payment_for`, `event_title` |
| `milestone_paid` | `BREVO_TEMPLATE_MILESTONE_PAID` | `document_hub.services.mark_milestone_paid` (immediate) | `label`, `amount`, `paid_on`, `event_title` |
| `user_credentials` | `BREVO_TEMPLATE_USER_CREDENTIALS` | `accounts.utils.send_user_credentials_email` (immediate) | `display_name`, `user_email`, `temporary_password`, `login_url` |
| `password_reset` | `BREVO_TEMPLATE_PASSWORD_RESET` | `accounts.utils.send_password_reset_email` (immediate) | `user_first_name`, `code`, `expires_in_minutes` |
| `inquiry_received` | `BREVO_TEMPLATE_INQUIRY_RECEIVED` | `inquiries.services.create_inquiry` (immediate) | `first_name` |
| `inquiry_submitted_internal` | `BREVO_TEMPLATE_INQUIRY_SUBMITTED_INTERNAL` | `inquiries.services.create_inquiry` (immediate) | `recipient_name`, `first_name`, `last_name`, `email`, `phone_number`, `contact_mode`, `event_type`, `desired_location`, `preferred_start_date`, `preferred_end_date`, `budget`, `details`, `submitted_at`, `inquiry_id` |

Note: none of the four `document_hub` types above fire from
`seed_engagement_documents` (the boilerplate FAQ cloned onto every new
engagement) — only documents/invoices/receipts a staff member actively creates
afterward, and milestones staff mark paid, notify the client. See
`apps/document_hub/README.md`.

`user_credentials`/`password_reset` used to bypass this app entirely (direct
SMTP sends from `apps/accounts/tasks.py`, ungated by
`NotificationTypeSettings`). They're now on the same pipeline as everything
else — same retry/backoff, same admin audit trail, same per-type toggle.

`inquiry_submitted_internal` is the first type addressed to **staff** rather
than a client — it alerts whoever has `receives_inquiry_alerts` ticked, one
email per recipient. Both inquiry types are also the first to be queued with
`recipient_user` and `engagement` **unset**: a lead has no account and no
engagement, and the send path never dereferences either field, so they ride the
existing pipeline with no transport change. See `apps/inquiries/README.md`.

## Why "new reminder" is immediate but the digests are periodic
A new reminder has a clear creation trigger — notify right away. Payment-due
and meeting-prep have no equivalent single moment; they're genuinely
time-relative ("this becomes relevant N days before X"), so they're periodic
lookahead scans run from the `daily_maintenance` cron group instead. Each scan
stamps the source row (`PaymentMilestone.reminder_sent_at`,
`Meeting.prep_reminder_sent_at`) **before** queueing the email, so re-running
the scan daily doesn't re-send while the same thing is still pending within the
lookahead window — and so a failure can never leave the mail sent with the
marker unwritten, which is the order it used to be in. Deliberately
simple: adding a fresh required prep field after the digest already fired
does **not** reset `prep_reminder_sent_at` — that meeting just won't get a
second nudge before it happens. Revisit if that turns out to matter in practice.

## Tasks (`apps/notifications/tasks.py` — generic machinery only)
- `send_notification_task(notification_id, force=False)` — **exactly one attempt
  per dispatch**, up to `MAX_ATTEMPTS` (3) across dispatches, then `ABANDONED`.
  There is deliberately no retry loop inside a dispatch: `send_now()` increments
  `attempt_count` on every call, so three tries in one dispatch would spend the
  whole budget and the sweep (`attempt_count__lt=MAX_ATTEMPTS`) would then skip
  the row for ever. **Not** gated by `ScheduledTaskSettings` — see that model's
  section above for why. `force=True` bypasses the Brevo circuit breaker (used by
  the sweep + drain).
- `retry_failed_notifications_task` — the recovery sweep, and the **only** retry
  path. Runs from the `notification_retry` cron group every 10 minutes and
  re-drives two populations with `force=True`:
  - `FAILED` with `attempt_count < MAX_ATTEMPTS`;
  - **`QUEUED` older than `STRANDED_AFTER` (10 min)** — a row whose in-process
    dispatch was lost to a deploy, an OOM or a restart. The age floor matters: without
    it the sweep would race the thread pool and re-send something in flight.
- `cleanup_old_notifications_task` — weekly purge of terminal rows (`SENT` **and**
  `ABANDONED`) older than 90 days. `ABANDONED` used to be excluded, which meant
  the rows most likely to hold a stack trace — and, before the redaction below, a
  plaintext temporary password — were the ones kept for ever.

Domain-specific digest scans live in the apps that own that data, not here:
`document_hub.tasks.payment_due_digest_task`, `meetings.tasks.meeting_prep_digest_task`.

## Statuses

| Status | Meaning | Re-driven by the sweep? |
|---|---|---|
| `queued` | Created and committed; its dispatch has been handed to the thread pool | Only once older than `STRANDED_AFTER` (10 min) — see the sweep |
| `sent` | Brevo accepted it. Terminal | No |
| `failed` | Genuinely attempted and failed. `error_message` says why, `attempt_count` is spent | Yes, while attempts remain |
| `deferred` | **Never attempted** — the Brevo breaker was open when its turn came. `attempt_count` stays 0 | Yes, identically to `failed` |
| `abandoned` | We have stopped trying: budget spent, or past the give-up window. Terminal | No |

`deferred` exists because the old labelling lied. A row the breaker parked was
recorded as `failed` with `error_message="Deferred: Brevo is currently
unavailable."` and `attempt_count=0` — correct behaviour (don't throw mail at a
dead API, don't spend an attempt on an outage) under a label that told anyone
reading the admin that a send had been tried and had failed, when nothing had been
tried. The sweep treats the two identically (`tasks.RETRYABLE_STATUSES`), so this
changed no behaviour; migration `0018` relabelled the existing rows.

The staff `?status=` filter on `GET /notifications/` validates against
`NotificationStatus.values`, so `deferred` is selectable there with no code change.
Clients only ever see `sent`.

## Auth secrets in `context`
`Notification.context` is the exact params dict handed to Brevo, so two templates
hold a live credential: `password_reset` (the 6-digit code) and
`user_credentials` (the generated temporary password). Three things keep them from
resting in the database:

- `services.send_now` overwrites `context` with `REDACTED_CONTEXT` on a successful
  send, and `send_notification_task` / the sweep's give-up pass do the same when a
  row goes `ABANDONED` — i.e. on every **terminal** state.
- `NotificationAdmin` **excludes** the field outright.
- Migration `0017_scrub_auth_notification_context` scrubbed what was already
  stored.

A `QUEUED` or `FAILED` row deliberately keeps its secret: the sweep re-reads
`context` to re-send, and scrubbing there would mail a client a credentials email
with no credential in it.

## Scheduling
There is no beat and no broker. `python manage.py run_scheduled <group>` runs one
cadence group to completion and exits; platform cron invokes it (three services —
see `RUNBOOK.md`). Group membership is in
`apps/core/management/commands/run_scheduled.py`.

`ScheduledTaskSettings` still gates whether a task *does work* when it fires (an
in-code, fail-open guard, checked as each task's first statement); the cron
schedule controls *when* it fires. The two compose exactly as they did before —
what changed is that timing is no longer a `PeriodicTask` row in the admin.

`notification_retry`'s 10-minute cadence is load-bearing rather than a
preference: it is the only retry path for a failed send, and a password-reset code
lives 30 minutes (`accounts.utils.RESET_CODE_TTL_MINUTES`). Widening it trades
against password-reset recovery.

## The Brevo client is built once

`services._brevo_api()` caches the SDK client behind a lock. `Configuration()`,
`ApiClient()` and `TransactionalEmailsApi()` used to be constructed inside every
send, which also meant a fresh `urllib3.PoolManager` per email — so every send
paid for a new TLS handshake and the connection pool was never reused. Cheap once,
but it is per-thread churn in a pool whose whole job is sending mail.

Safe to share across the background pool: the transport is a
`urllib3.PoolManager`, which is thread-safe by design, and the client's own state
is written at construction and only read afterwards. `ApiClient` does lazily build
a `ThreadPool`, but only to service `async_req=True` calls — every call here is
synchronous, so it is never created.

`connection_pool_maxsize` is pinned to `BACKGROUND_MAX_WORKERS + 4` rather than
left at the SDK's `cpu_count() * 5`, which is a number about the machine — and
inside a container, about the *host's* machine — instead of about how many senders
we actually run.

The API key is read once, so rotating `BREVO_API_KEY` needs a restart (already
true of every other setting). `reset_brevo_client()` exists for tests and for a
shell after changing the key.

## Brevo health & outage detection
`ServiceHealthState` (one row per external service, seeded `service="brevo"`)
holds Brevo's current up/down status — visible in admin, and the alert-dedup
mechanism: the high-severity `event=brevo_outage` / `event=brevo_recovered`
signal is emitted only on a status *transition* (`record_failure` /
`record_success` return whether one happened), so a multi-hour outage produces
exactly one alert. Two things maintain it:

`services.send_now` records every real send outcome, and deliberately does NOT
burn `attempt_count` on failures that happen while Brevo is known-down, so an
outage can't march queued mail to permanent failure.

This used to be one of *two* writers — there was also an active probe hitting
Brevo's account endpoint and status page every ~5 minutes, so an outage was caught
before an email was even sent. It was removed with Celery (288 runs and 576
outbound HTTPS calls a day, for a few minutes' earlier warning).

While `ServiceHealthState` is `down`, `send_notification_task` (with
`force=False`, the normal path) **parks** the send instead of hammering a dead
API; the sweep passes `force=True` so it genuinely re-attempts (half-open),
draining the backlog and — now that the probe is gone — being the thing that
detects recovery.

**`DOWN_STALE_AFTER` is what makes purely passive detection safe.** A `down`
verdict is cleared only by a *successful* send, and while it is set every ordinary
send parks itself without attempting — so the only thing that can clear it is a
forced send from the sweep. If that sweep is switched off or its cron service is
broken, a stale `down` row would park **every** notification in the platform,
indefinitely, with nothing surfacing why. Past 30 minutes, `is_down()` reports
False and the next real send is allowed to probe Brevo itself. The row is not
rewritten, so the admin still shows the last real verdict.
See docs/OBSERVABILITY_STANDARD.md §6.

## Tests
`python manage.py test notifications` — queue creates a row + dispatches the
task, unknown `template_name` rejected at queue time, a successful send
calls Brevo (mocked) with the right `template_id`/`params` and updates
`status`/`sent_at`, an unconfigured template ID and a Brevo API failure both
fail cleanly (never raise) and record `error_message`.

---

## In-portal history — `GET /notifications/`

The read side of what was previously an email-only pipeline. **Nothing here
writes** — notifications are still created exclusively by
`services.queue_notification()`.

| Caller | Sees |
|---|---|
| Client | their own **sent** notifications |
| Staff | `?portal_id=<uuid>` for one client's history, or omit it for a platform-wide feed |

Filters: `?type=` (repeatable, matches `template_name`), `?status=` (**staff
only** — `queued`/`sent`/`failed`), `?limit=` (default 50, max 200).

### Two deliberate exclusions — do not "fix" these

**1. Auth emails never appear.** `user_credentials` and `password_reset` are
filtered out (`views.AUTH_ONLY_TYPES`) for every caller, staff included. They're
account-security messages rather than portal activity — and see below.

**2. `context` is never serialised.** The `context` JSONField holds the Brevo
template variables, and for those two auth templates that means a
**`temporary_password`** and a password-reset **`code`** (see
`accounts/utils.py`). Exposing `context` on any endpoint would leak live
credentials. The serializer omits it outright — belt to the braces of the type
filter above. `error_message` and `attempt_count` are omitted too: internal
delivery diagnostics belong in the admin, not a client payload.

### Why clients only see `sent`
A `queued` or `failed` notification never reached them, so listing it as
"history" would be a lie. Staff can filter by status precisely because they're
debugging delivery, which is the opposite need.

`type_display` gives the human label (`"Payment due"`) for the raw
`template_name` (`"payment_due"`) so the frontend doesn't hardcode the map; an
unknown `template_name` falls back to the raw value rather than 500-ing a
history read.

---

## Retry policy — when we stop trying

| Setting (`tasks.py`) | Value | Meaning |
|---|---|---|
| `MAX_ATTEMPTS` | 3 | total delivery attempts per notification, across dispatches |
| `STRANDED_AFTER` | 10 min | how long a `QUEUED` row may sit before the sweep assumes its dispatch was lost |
| `GIVE_UP_AFTER_DAYS` | 7 | absolute ceiling for a row that never got a real attempt |
| `PRIORITY_TEMPLATES` | `password_reset`, `user_credentials` | swept before everything else |

**There is no retry delay any more**, and its absence is deliberate.
`RETRY_DELAY_SECONDS` (a flat 300s, paired with Celery's `self.retry`) is gone:
`send_now()` increments `attempt_count` on every call, so a retry loop inside one
dispatch would spend the whole budget and the sweep — which filters
`attempt_count__lt=MAX_ATTEMPTS` — would then skip the row for ever. The interval
between attempts is now simply the sweep's cadence (10 min), which is both easier
to reason about than a backoff curve *and* the thing that survives a process
restart.

`PRIORITY_TEMPLATES` is swept first because the sweep runs inline in a cron
process with a finite lifetime: if it is killed part-way through, the thing that
should survive being dropped is the digest, not the reset code that expires in 30
minutes.

**`FAILED` vs `ABANDONED`.** `FAILED` means "will be retried"; **`ABANDONED` is
terminal** — we have stopped trying. The sweep re-drives `FAILED` and stranded
`QUEUED` rows only, so an abandoned row is never picked up again. Abandoned rows
are **kept, not deleted** (for 90 days), so the failure stays queryable in the
admin instead of vanishing.

A notification reaches `ABANDONED` two ways:
1. **Attempt budget spent** — `send_notification_task` marks it abandoned once
   `attempt_count >= MAX_ATTEMPTS`.
2. **Past the give-up window** — the sweep abandons any retryable row older than
   `GIVE_UP_AFTER_DAYS`, whatever its attempt count. This exists because the
   Brevo-outage path deliberately does **not** spend an attempt (right for a
   blip — a multi-hour outage shouldn't march every queued email to its limit
   and strand it), which without an age ceiling would let a parked row be
   re-queued *forever* if Brevo never came back.

Together those two stops mean nothing is retried indefinitely, while a normal
outage still recovers cleanly via `_drain_after_recovery` (which reuses the
sweep, so it inherits both stops for free — and no-ops when the sweep itself is
what detected the recovery, since that cycle *is* the drain).

> **There is no task-lifecycle log line to read any more.** Celery used to print
> a `Task notifications.retry_failed … succeeded` line every hour whether it
> re-queued 50 rows or zero, which was reliably mistaken for evidence of work.
> The sweep now logs only when it actually does something (`abandoned N
> notification(s) …`), and the authoritative answer to "is anything stuck?" is the
> `Notification` table itself — see RUNBOOK.md.
