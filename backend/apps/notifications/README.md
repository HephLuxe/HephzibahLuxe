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
(queued/sent/failed/**abandoned** — see "Retry policy" below), `sent_at`,
`error_message`, `attempt_count`.

## Model — `NotificationTypeSettings`
Per-`template_name` on/off switch, configured in the Django admin (no API) —
e.g. turn off "payment due" reminders without silencing everything else. One
row per `NotificationType`; `NotificationTypeSettings.is_enabled(template_name)`
is the check `queue_notification` applies. **A template with no row is
treated as enabled** (fail-open) — so a newly added `NotificationType` works
immediately, before anyone remembers to add a settings row for it. A data
migration seeds a row (enabled) for every type that exists today.

`retry_failed_notifications_task` (the hourly failed-notification sweep)
applies this same per-type check itself, since it re-queues existing rows
directly rather than going through `queue_notification`.

## Model — `ScheduledTaskSettings`
A **different** on/off switch, orthogonal to `NotificationTypeSettings`:
whether a background/periodic Celery task runs **at all**, independent of
whether any individual notification it might produce is itself enabled. Same
admin-only, fail-open shape (`is_task_enabled(task_key)` — no row means the
task runs normally). Gates the digest/sweep tasks, *not* the low-level
per-notification sender (`send_notification_task`) — see the reasoning in
that task's own docstring for why it's deliberately excluded (gating it
would strand already-approved, already-queued notifications rather than
stop anything meaningful).

| `task_key` | Task | Trigger |
|---|---|---|
| `payment_due_digest` | `document_hub.tasks.payment_due_digest_task` | daily beat |
| `meeting_prep_digest` | `meetings.tasks.meeting_prep_digest_task` | daily beat |
| `notifications_retry_failed` | `notifications.tasks.retry_failed_notifications_task` | hourly beat |
| `notifications_cleanup_old` | `notifications.tasks.cleanup_old_notifications_task` | weekly beat |
| `notifications_brevo_health_probe` | `notifications.tasks.brevo_health_probe_task` | beat, ~5 min |
| `event_details_notification` | `events.tasks.send_event_details_updated_notification_task` | debounced, per-edit — see `apps/events/README.md` |

For `event_details_notification`, the gate is checked in **two** places:
`events.services.schedule_event_details_notification` skips calling
`apply_async` entirely when disabled (no point scheduling a task that will
just no-op), plus a defensive check inside the task itself in case it was
already scheduled before being toggled off mid-flight.

## Templates to build in Brevo

Each row's params are exactly the `context` dict its caller builds — build
a Brevo template expecting these merge fields, then set its ID in
`BREVO_TEMPLATE_<NAME>`.

| `template_name` | Settings var | Fired by | Params |
|---|---|---|---|
| `new_reminder` | `BREVO_TEMPLATE_NEW_REMINDER` | `reminders.services.create_reminder` (immediate) | `title`, `description`, `priority_display`, `due_date`, `link_url`, `link_label` |
| `payment_due` | `BREVO_TEMPLATE_PAYMENT_DUE` | `document_hub.tasks.payment_due_digest_task` (daily beat) | `label`, `amount`, `due_date`, `event_title` |
| `meeting_prep_due` | `BREVO_TEMPLATE_MEETING_PREP_DUE` | `meetings.tasks.meeting_prep_digest_task` (daily beat) | `meeting_title`, `meeting_date`, `incomplete_count` |
| `phase_advanced` | `BREVO_TEMPLATE_PHASE_ADVANCED` | `portal.services.set_phase` / `advance_phase` (immediate) | `phase_display`, `event_title` |
| `event_details_updated` | `BREVO_TEMPLATE_EVENT_DETAILS_UPDATED` | `events.services.schedule_event_details_notification` (**debounced**) | `event_title`, `what` |
| `document_added` | `BREVO_TEMPLATE_DOCUMENT_ADDED` | `document_hub.views.create_document` (immediate) | `document_title`, `category_display`, `event_title` |
| `invoice_issued` | `BREVO_TEMPLATE_INVOICE_ISSUED` | `document_hub.views.create_invoice` (immediate) | `invoice_number`, `amount`, `due_on`, `event_title` |
| `receipt_issued` | `BREVO_TEMPLATE_RECEIPT_ISSUED` | `document_hub.views.create_receipt` (immediate) | `receipt_number`, `amount`, `payment_for`, `event_title` |
| `milestone_paid` | `BREVO_TEMPLATE_MILESTONE_PAID` | `document_hub.services.mark_milestone_paid` (immediate) | `label`, `amount`, `paid_on`, `event_title` |
| `user_credentials` | `BREVO_TEMPLATE_USER_CREDENTIALS` | `accounts.utils.send_user_credentials_email` (immediate) | `display_name`, `user_email`, `temporary_password`, `login_url` |
| `password_reset` | `BREVO_TEMPLATE_PASSWORD_RESET` | `accounts.utils.send_password_reset_email` (immediate) | `user_first_name`, `code`, `expires_in_minutes` |

Note: none of the four `document_hub` types above fire from
`seed_engagement_documents` (the boilerplate FAQ cloned onto every new
engagement) — only documents/invoices/receipts a staff member actively creates
afterward, and milestones staff mark paid, notify the client. See
`apps/document_hub/README.md`.

`user_credentials`/`password_reset` used to bypass this app entirely (direct
SMTP sends from `apps/accounts/tasks.py`, ungated by
`NotificationTypeSettings`). They're now on the same pipeline as everything
else — same retry/backoff, same admin audit trail, same per-type toggle.

## Why "new reminder" is immediate but the digests are periodic
A new reminder has a clear creation trigger — notify right away. Payment-due
and meeting-prep have no equivalent single moment; they're genuinely
time-relative ("this becomes relevant N days before X"), so they're periodic
lookahead scans via Celery beat instead. Each scan stamps the source row
(`PaymentMilestone.reminder_sent_at`, `Meeting.prep_reminder_sent_at`) the
first time it notifies, so re-running the scan daily doesn't re-send while
the same thing is still pending within the lookahead window. Deliberately
simple: adding a fresh required prep field after the digest already fired
does **not** reset `prep_reminder_sent_at` — that meeting just won't get a
second nudge before it happens. Revisit if that turns out to matter in practice.

## Tasks (`apps/notifications/tasks.py` — generic machinery only)
- `send_notification_task(notification_id, force=False)` — single send, retries
  up to 3x with exponential backoff (60s/300s/900s), `acks_late=True`. **Not**
  gated by `ScheduledTaskSettings` — see that model's section above for why.
  `force=True` bypasses the Brevo circuit breaker (used by the sweep + drain).
- `retry_failed_notifications_task` — hourly sweep, re-queues anything
  `FAILED` with `attempt_count < 3` (with `force=True`).
- `cleanup_old_notifications_task` — weekly purge of `SENT` rows older than
  90 days.
- `brevo_health_probe_task` — active Brevo reachability probe (beat, ~5 min),
  gated by `ScheduledTaskSettings`. See "Brevo health & outage detection" below.

Domain-specific digest scans live in the apps that own that data, not here:
`document_hub.tasks.payment_due_digest_task`, `meetings.tasks.meeting_prep_digest_task`.

## Beat schedule
DB-backed via **`django-celery-beat`** (`DatabaseScheduler`), so every periodic
task's timing is admin-editable (Django admin → **Periodic Tasks**) with no
redeploy — and, critically, so it matches the home-server `celerybeat@` systemd
unit, which hardcodes `--scheduler django_celery_beat.schedulers:DatabaseScheduler`.
A static `CELERY_BEAT_SCHEDULE` would be silently ignored under that scheduler,
stranding every periodic task. The shipped defaults (the four jobs above + the
Brevo health probe) are installed by the idempotent
`python manage.py seed_periodic_tasks` command (in `apps/core`) — run it once
per environment after `migrate`. **Requires a separate `celery -A config beat`
process** running alongside the worker.

`ScheduledTaskSettings` still gates whether a task *does work* when it fires (an
in-code, fail-open guard); `django-celery-beat` controls *when/whether* it fires.
The two compose.

## Brevo health & outage detection
`ServiceHealthState` (one row per external service, seeded `service="brevo"`)
holds Brevo's current up/down status — visible in admin, and the alert-dedup
mechanism: the high-severity `event=brevo_outage` / `event=brevo_recovered`
signal is emitted only on a status *transition* (`record_failure` /
`record_success` return whether one happened), so a multi-hour outage produces
exactly one alert. Two things maintain it:

- **Passive** — `services.send_now` records every real send outcome. It also
  does NOT burn `attempt_count` on failures that happen while Brevo is
  known-down, so an outage can't march queued mail to permanent-failure.
- **Active** — `tasks.brevo_health_probe_task` (beat, ~5 min, gated by
  `ScheduledTaskSettings("notifications_brevo_health_probe")`) pings Brevo's
  account endpoint + status page, so an outage is caught before an email is sent.

While `ServiceHealthState` is `down`, `send_notification_task` (with
`force=False`, the normal path) **parks** the send instead of hammering a dead
API; the hourly sweep and the drain-on-recovery pass `force=True` so they
genuinely re-attempt (half-open), draining the backlog and detecting recovery
even if the probe is disabled. See docs/OBSERVABILITY_STANDARD.md §6.

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
| `MAX_ATTEMPTS` | 3 | total delivery attempts per notification |
| `RETRY_DELAY_SECONDS` | 300 | **flat** 5-minute wait between attempts |
| `GIVE_UP_AFTER_DAYS` | 7 | absolute ceiling for a row that never got a real attempt |

The delay is deliberately **flat, not exponential** — three tries five minutes
apart is easy to reason about, whereas a growing backoff makes "when does this
stop?" impossible to answer at a glance.

**`FAILED` vs `ABANDONED`.** `FAILED` means "will be retried"; **`ABANDONED` is
terminal** — we have stopped trying. The hourly sweep only scans `FAILED`, so an
abandoned row is never picked up again. Abandoned rows are **kept, not deleted**,
so the failure stays queryable in the admin instead of vanishing.

A notification reaches `ABANDONED` two ways:
1. **Attempt budget spent** — `send_notification_task` marks it abandoned on the
   final failed attempt.
2. **Past the give-up window** — the sweep abandons any `FAILED` row older than
   `GIVE_UP_AFTER_DAYS`, whatever its attempt count. This exists because the
   Brevo-outage path deliberately does **not** spend an attempt (right for a
   blip — a multi-hour outage shouldn't march every queued email to its limit
   and strand it), which without an age ceiling would let a parked row be
   re-queued hourly *forever* if Brevo never came back.

Together those two stops mean nothing is retried indefinitely, while a normal
outage still recovers cleanly via `_drain_after_recovery` (which reuses the
sweep, so it inherits both stops for free).

> **The hourly `Task notifications.retry_failed … succeeded` log line does not
> mean anything was retried.** That's Celery's task-lifecycle logging at
> `-l info`; the beat job fires hourly and logs identically whether it re-queued
> 50 rows or zero. Run the worker at `-l warning` to silence it — see RUNBOOK.md.
