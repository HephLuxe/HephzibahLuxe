# Hephzibah Luxe

**The backend for a luxury event-planning practice — a client portal where a celebrant and the planning team run one event end to end: a six-phase planning journey, meetings with structured prep, a contract and payment hub, vendor budgets, and every client-facing email that ties them together.**

Django 5.2 · DRF · PostgreSQL · Redis (cache) · Cloudflare R2 · Brevo

---

## The problem this solves

High-touch event planning runs on WhatsApp threads, shared Drive folders, and a spreadsheet somebody renamed. That works right up until it doesn't:

> *"Did we send them the service agreement, or just talk about it?"*
> *"Which quote did they approve — the one from the 14th or the 19th?"*
> *"They said they'd bring reference images to the meeting. Did they?"*

The information exists. It's just scattered across six tools, none of which knows the others exist, and the client only sees the fragments that happened to be forwarded to them.

Hephzibah Luxe collapses that into **one authoritative surface per event**. Staff work in the Django admin and the staff API; the client sees a portal with exactly the slice they're meant to see, and nothing else. The planning phase they're in, the meetings they need to prepare for, the documents they've signed, the money that's paid and what's next — all derived from one database, with an email pipeline that tells them when something changed.

The architectural bet the whole codebase makes: **a client is not an event.** Clients come back. A portal is a permanent identity; each event they plan is a separate, self-contained engagement that hangs off it. Everything else follows from that.

---

## What's in this repository

The **backend API** — the complete platform surface: data model, permission layer, staff workflows, client reads, document and payment hub, background jobs, transactional email, and ops tooling. It is API-only (JSON, JWT); the client portal frontend is a separate app that consumes it.

`13 apps · 34 models · 95 routes · ~80 tests · 13 email templates · 5 scheduled jobs`

---

## Feature overview

### 1. Portal ↔ Engagement — the model that makes returning clients work

Most event tools model a client *as* their event. Then the client books a second event and you're picking between two bad options: duplicate their account, or overwrite their history.

Here they're separate:

```
User ──1:1──► ClientPortal ──1:N──► EventEngagement ──1:1──► Event
              (permanent identity)   (one planning context)
```

- **`ClientPortal`** is the client's permanent identity — created once, never event-aware. It owns their welcome message and their assigned team.
- **`EventEngagement`** is the bridge, and it owns *all* the planning state: `current_phase`, `phase_details`, `contacts_locked`, `event_details_locked`, the reference-code segment. Meetings, conversations, reminders, documents, invoices, and receipts all hang off the engagement — not the portal, and not the user.
- Exactly **one engagement is `is_active`** per portal. `activate_engagement(portal, event)` atomically deactivates the old one and promotes the new.

The payoff: a client who did a wedding in 2024 and comes back for an anniversary in 2026 gets a fresh workspace with its own phase, its own documents, its own payment schedule — while every 2024 record stays intact, queryable, and correctly attributed. Nothing is duplicated and nothing is destroyed.

It also lets staff **pre-stage a future event** — every event gets its own engagement at creation (active only if the portal has none), so meetings and documents can be prepared before the client is switched over to it.

### 2. The six-phase planning journey — with attribution and auto-lock

`Connect → Align → Curate → Envision → Orchestrate → Deliver`

The engagement's `current_phase` is the single source of truth for where the client is: it drives the progress bar, which conversation threads surface by default, which guidance copy shows, and what's locked.

Two things make it more than an enum:

- **Attribution.** `phase_updated_by` / `phase_updated_at` record who advanced the phase and when, surfaced as "Last updated by …". The display name resolves through the account's *current* name, so a staff member's rename propagates everywhere rather than freezing whatever string was captured at write time.
- **Auto-lock.** Late-stage edits to event details and contacts are a real operational hazard — a venue address changing after the run-of-show is printed. `PortalSettings` (admin-only singleton) lets staff set a trigger phase and which locks to apply; reaching that phase sets `event_details_locked` / `contacts_locked` automatically. Deliberately **lock-on-reach only** — it never auto-unlocks, because an automatic unlock would silently undo a staff member's deliberate decision. Manual unlock is always available.

The two locks are **separate flags on purpose**. Freezing the venue doesn't mean freezing the contact list, and conflating them would force staff to choose between two unrelated restrictions.

Meetings also carry a `phase` field — but that one is purely an organizational label. It groups and filters; it never restricts access. Every meeting stays visible to the client regardless of the portal's current phase.

### 3. Deep links point at objects, not URL strings

This is the piece worth stealing.

A reminder or a conversation often needs to say *"go look at this"*. The obvious implementation is a `link_url` text field a staff member types. That implementation is quietly broken in three separate ways, and this codebase refuses all three by storing a **reference to the actual object** and deriving the URL at read time from a registry (`apps/core/deeplinks.py`):

| Failure mode of a typed URL | What the registry does |
|---|---|
| The id belongs to **another client's portal** | Every target type knows how to report its own engagement. A target outside the linking record's engagement is rejected at create/edit time with `validation_error`. A hand-typed `?conversationId=<uuid>` is unverifiable — the backend would hand one client another's id without noticing. |
| The id names a **row that doesn't exist** (typo, deleted later) | Malformed or missing ids are rejected up front. A target deleted *afterwards* resolves to `null`, so the card renders without a link — instead of a 404 the client discovers by clicking. For conversation pills, the dead entry is dropped from the response entirely. |
| The frontend **renames a route** | One line in `TARGET_TYPES`. Not a data migration over thousands of stored strings. |

Eleven target types are linkable today — `conversation`, `meeting`, `prep_item`, `event`, `event_day`, `event_contact`, `client_document`, `invoice`, `receipt`, `payment_milestone`, `budget_payment`. Adding a twelfth is a single `TargetSpec` entry: model label, URL builder, engagement resolver, default label. Nothing else changes.

The same registry builds absolute URLs for emails (`absolute_url`, `login_url`) from one `FRONTEND_BASE_URL` — because a relative path in an inbox is meaningless, and two env vars naming one deployment is how staging links end up in production emails.

**Deliberately defensive:** an object whose engagement can't be resolved returns `None`, and `None` is *never* read as "allowed." Unresolvable means not linkable, always.

### 4. Notifications: a delivery pipeline, not `send_mail()` calls

Thirteen transactional email types go through **one** function — `notifications.services.queue_notification()`. Nothing anywhere in the codebase calls Brevo directly, which is what makes the rest of this possible from one place instead of thirteen scattered copies.

Every type maps 1:1 to a template built in the Brevo dashboard; the `context` dict becomes Brevo's template `params` directly, with `Decimal`/`UUID`/`date`/`datetime` auto-coerced to JSON-safe primitives so no call site hand-casts.

**Retry and audit**
- Every send is a `Notification` row — an audit trail queryable in the admin, with `status`, `attempt_count`, `error_message` and `sent_at`. With no broker to inspect, that row *is* the queue: it is the durable record the whole design rests on.
- **One attempt per dispatch, up to 3 total**, then a distinct **`abandoned`** status rather than a silent give-up. There is deliberately no retry loop inside a dispatch: `send_now()` increments `attempt_count` on every call, so three tries in one dispatch would spend the whole budget and the sweep — which filters `attempt_count__lt=MAX` — would then skip the row for ever.
- **A 10-minute sweep is the only retry path**, and it re-drives two populations: rows that are `FAILED` with attempts remaining, *and* rows stranded in `queued` for more than 10 minutes because their in-process dispatch was lost to a deploy or a restart. The second half is what makes a broker-less dispatch safe — see [§15](#15-no-celery--in-process-work-plus-platform-cron).
- A weekly purge drops terminal rows (`sent` **and** `abandoned`) older than 90 days.

**Auth secrets never rest in the database.** `context` is the exact params dict handed to Brevo, so for `password_reset` it holds a live 6-digit code and for `user_credentials` a generated temporary password. Both are redacted the moment the row reaches a terminal state, and `context` is excluded from the Notification admin entirely. A `queued` or `failed` row deliberately keeps its secret — the sweep re-reads it to re-send.

**A circuit breaker around Brevo** — the part that separates this from a retry loop:

- `ServiceHealthState` holds Brevo's live up/down status, maintained from the outcome of real sends — three consecutive failures trip it.
- While Brevo is known-down, the normal send path **parks** rather than hammering a dead API — and critically, **does not burn `attempt_count`**. Without that, a two-hour outage would march every queued email to permanent failure through no fault of its own.
- The sweep passes `force=True` to genuinely re-attempt (half-open), so it both drains the backlog *and* is what detects recovery.
- **A `down` verdict expires after 30 minutes.** This is the guard that stops the breaker from becoming the outage: `down` is cleared only by a *successful* send, and while it is set every ordinary send parks itself without attempting — so a stale row would mute the entire platform indefinitely. Past the ceiling `is_down()` reports False and the next real send probes Brevo itself. The row is not rewritten, so the admin still shows the last real verdict.
- The high-severity `brevo_outage` / `brevo_recovered` log events fire **only on a status transition**, so a multi-hour outage produces exactly one alert instead of one per failed send.

There used to be an active probe here too — a scheduled job pinging Brevo's account endpoint every five minutes so an outage was caught *before* someone's password-reset email hit it. It was removed with Celery: 288 scheduled runs and 576 outbound HTTPS calls a day, to learn a few minutes earlier what the next real send would report. The staleness ceiling above is what makes purely passive detection safe.

**Two orthogonal admin toggles**, both fail-open (no row means enabled):
- `NotificationTypeSettings` — should *this kind of email* go out? Turn off "payment due" without silencing anything else.
- `ScheduledTaskSettings` — should *this background job* run at all? Independent of whether the emails it might produce are enabled.

Fail-open is a deliberate choice: a newly added notification type works immediately, before anyone remembers to create a settings row for it.

### 5. Trailing debounce — one email per editing session, not one per save

Staff editing an event touch six fields and three event days in one sitting. Naively, that's nine "your event details were updated" emails.

A fixed delay doesn't fix it either — it just puts the editor on a clock, pressuring them to finish before it fires.

So `schedule_event_details_notification` implements a **trailing debounce**, and the whole of its state is a row:

1. Every edit stamps three columns on the engagement — a fresh token, `event_details_notify_due_at = now + window`, and `event_details_notify_what` describing the change. An edit during the window simply pushes `due_at` further out and overwrites `what`.
2. A sweep (`events.tasks.dispatch_due_event_details_notifications`, in the 10-minute cron group) picks up everything whose `due_at` has passed. It **clears the schedule first**, in one UPDATE filtered on `due_at IS NOT NULL`, and only sends if that UPDATE claimed the row — so it is idempotent, two overlapping sweeps cannot double-send, and an edit landing mid-send re-stamps its own later `due_at` and gets its own email.

As long as an admin keeps editing, no email goes out. Once they stop, exactly one does. The window is `PortalSettings.event_details_notify_debounce_seconds` — **admin-editable, no redeploy**.

**This is the one place the Celery removal changed a design rather than a mechanism**, and it fixed a bug rather than working around one. Before, the token was a column but *"and send at T+900s"* lived only in the broker's ETA queue and `what` only as a task argument — half the state in Postgres, half in a message. A worker restart or a deploy inside the window could drop the email outright, and recovery depended on `visibility_timeout` redelivery: a Celery implementation detail rather than a guarantee we owned. The cost is precision — a 15-minute debounce swept every 10 minutes lands 15–25 minutes after the last edit rather than exactly 15. For *"the planner finished editing, tell the client"* that is not a meaningful difference, and the debounce now survives a deploy.

### 6. Two-tier media storage — signed documents, public images

Client documents and display images have opposite requirements, and serving both from one bucket policy breaks one of them.

- **Documents** (service agreements, quotations, invoices, receipts, budget receipts, meeting prep uploads) live on the default storage with **signed, expiring URLs** (1h default). A leaked or forwarded link stops working. This never changes.
- **Display images** (event covers, event-day images, contact photos) are rendered inline and get cached inside already-fetched event JSON. A 1-hour signature would turn every one of them into a broken image an hour after page load. They're served from a **separate public R2 bucket / custom domain** with unsigned, long-lived URLs.

Two implementation details that matter:

- The image fields take `storage=select_public_media_storage` — a **callable**, so Django records only the reference in migrations and resolves the actual backend at boot from current settings. Flipping `USE_R2_STORAGE`, or configuring a public bucket months later, needs **no new migration**.
- **Fail-safe, not fail-broken.** If R2 is on but no public bucket is configured yet, images fall back to the default *signed* storage rather than emitting unsigned URLs against a private bucket (which would 403). The worst case is "images work, with a 1h signature." Never "images break."

There is deliberately **no local-disk media path** at all. R2 in every real environment; in-memory storage under tests so CI runs without credentials or network. Nothing ever touches the filesystem.

### 7. Reference codes generated from the event itself

Every signable and financial record carries a system-generated code — `HL-PSW006-INV001` — that staff never type.

```
HL - PSW006 - INV 001
     │  │ │    │   └─ per engagement, per type, restarting at 001
     │  │ │    └───── C = Service Agreement · Q = Quotation · INV = Invoice · R = Receipt
     │  │ └────────── 006: the 6th wedding this business has ever done (global per-event-type counter)
     │  └──────────── W: Wedding (B birthday · C corporate · S social · O other)
     └─────────────── PS: initials — bride + groom for a wedding, honoree for a birthday
```

The `<segment>` (`PSW006`) identifies the **engagement**, is assigned once, and is frozen — so two different birthdays with the same initials stay distinct via the global counter (`…AAB002…`, `…AAB003…`). Counters are race-safe behind a `ReferenceCounter` row.

Generation is applied by **`pre_save` signals**, not in the view — so the API path, the Django admin, the auto-seeder, and a shell session all produce a code. There is no path that creates a codeable record without one. The fields are read-only in both the API and the admin.

### 8. Contract payments: percentage is the source of truth

A `PaymentSchedule` splits `total_investment` into milestones — default **30% Deposit / 40% Phase 2 / 30% Final Payment**. The `percentage` is authoritative; the money `amount` is *derived* from it, never stored independently.

That single decision resolves the whole class of bugs where a schedule's parts stop summing to its whole:

- Creating a schedule auto-generates the 30/40/30 milestones — staff send only `total_investment`.
- Changing `total_investment` **re-splits** every percentage-based milestone automatically. Edit the total once; the phases follow.
- The **last milestone absorbs the rounding remainder**, so amounts sum to `total_investment` *exactly* (a 33/33/34 split of 100.00 → 33.00 / 33.00 / 34.00).
- Percentages are validated to sum to exactly 100, editable inline in the admin, with a "reset to default 30/40/30" action.

**Ad-hoc milestones** — a late-addition handling fee — carry no percentage. They're fixed-amount extras that sit *outside* the sum-to-100 rule and are untouched by re-splits.

And a boundary that's easy to blur and expensive to un-blur: `PaymentSchedule` is money the client owes **Hephzibah Luxe**. Vendor-by-vendor spend is `apps.budgets` (`BudgetPayment`) and has nothing to do with it. Two systems, one deliberately not modelling the other.

### 9. Meetings with derived prep completion

A meeting carries a checklist of `MeetingPrepItem`s, each with typed `PrepItemField`s — `checkbox`, `text`, `qa`, or `file_upload`. This is the one corner of the platform a client can write to.

**`is_completed` is computed, never set by hand.** The old "mark as done" endpoint was removed outright: a manually-set completion was a lie that the next state sync would silently revert.

The gate:
- An item with **≥1 required field** completes when every *required* field is answered. Optional fields never block.
- An **all-optional** item completes only when *every* field is answered — checklist behaviour, since "all optional" would otherwise mean "instantly complete."

Every response, upload, deletion, field add/remove, and type change re-runs `sync_prep_item_completion`. Deleting the last file on a required field flips the item back to incomplete automatically.

Details that came from real usage:
- **File uploads append by default** — a second inspiration image shouldn't wipe the first. Correcting one means `replace=true` (new files validated *first*, so a rejected upload never destroys the existing answer), deleting a single upload, or clearing the field entirely.
- **Changing a field's `field_type` clears its answer** and returns `cleared_answer=True` so the UI can warn — a text answer is meaningless once it's a file field.
- **A prep item cannot be created without at least one field.** Completion derives from fields; an item with none could never complete.
- Nested fields are validated **before anything is created**, so you never get a 201 with half the fields silently dropped.
- Upload limits: PDF / JPEG / PNG / WebP, 10 MB. Inspiration boards, not archives.
- Deleting an upload routes through a `post_delete` receiver that unregisters the document row and removes the blob — the one hook *every* delete path passes through, including cascades from field/item/meeting deletion that never touch view code.

Meetings also emit **`.ics` calendar files** (`GET /meetings/<id>/ics/`) and run through a validated status state machine (`upcoming → active → completed`, with `cancelled` / `rescheduled` transitions).

### 10. Two document systems, deliberately not merged

They look like duplication until you try to merge them.

| | `apps.documents` | `apps.document_hub` |
|---|---|---|
| **What** | Generic-FK registry of media produced as a **side effect** | Client-facing, staff-**authored** records |
| **Examples** | Event covers, contact photos, prep uploads, budget receipts | Service Agreement, Quotation, Welcome Booklet, Invoices, Receipts |
| **Created by** | `register_document()`, called internally | Staff, via the API or admin |
| **Endpoints** | One read route. **No write endpoints at all.** | Full CRUD + an aggregate read |
| **Lifecycle** | None — it's a catalog | Reference codes, signing state, due dates, payment status |

`apps.documents` never stores files. Owning apps save to their own `FileField` with their own upload path, then register the path string plus a `ContentType` generic FK back to the source instance. That decouples the apps entirely while still allowing "every file across this engagement" as one query. `object_id` is a `CharField`, not an integer — the registry has to accept both int-PK and UUID-PK sources.

**Auto-seeding is scoped by whether the content is genuinely boilerplate.** The FAQ and the welcome message are identical for every client, so staff configure them once on a singleton and every new engagement gets a copy (file **bytes cloned**, not a shared blob). The Service Agreement, Quotation, and Welcome Booklet are *not* boilerplate — a service agreement is a per-client legal document — so they are never auto-seeded, even though template slots exist for them. Seeding is idempotent, create-only, and skips categories the engagement already has, so a staff-deleted document is never silently re-added.

The seeded FAQ also deliberately **does not** fire a "document added" email. It's created the instant the engagement is, often before the client's first login — notifying then is noise stacked on the credentials email.

### 11. Aggregate endpoints — one call per page

`GET /event/<slug>/detail/` and `GET /document-hub/` each assemble an entire page in a single request: event + days + contacts grouped by category + per-category counts + the planning stage for *that specific event's* engagement (not just the portal's active one — a distinction that matters for a multi-event client viewing a past event).

The alternative is five round trips and a frontend that has to know how to stitch them. Writes return the **full** aggregate too, not just the changed fields, so the client stays in sync without a follow-up GET.

### 12. Security and access control

**Authentication** — JWT via SimpleJWT. Access 1 day, refresh 7 days, rotating with blacklist-after-rotation. The token response carries `first_name` / `last_name` / `force_password_change` inline so the frontend doesn't need a second call to know whether to redirect.

**Admin-driven registration** — clients never self-register. Staff create the account; a cryptographically secure temporary password is generated and mailed through the same notification pipeline as everything else; `force_password_change` is set, and middleware enforces it until they set a real one.

**Password reset in three phases** — a 6-digit code, **stored hashed**, valid 30 minutes, single-use (`is_used` set on confirm, so a code can't be replayed), with a **five-guess budget per code**. The request endpoint **always returns 200** regardless of whether the email exists, so it can't be used to enumerate accounts.

Three details that go together, because a six-digit code is only 10⁶ possibilities:

- **Hashed with the password hasher, not a bare digest.** The code used to sit in the clear next to the user it belonged to and the IP that asked for it, in rows nothing deleted — and the admin printed it in a column and let you search by it. SHA-256 wouldn't have helped: 10⁶ digests is a sub-second table. `make_password` costs ~69 ms per call, which is irrelevant against the `10/m` limit on that endpoint and is the whole point. Salted, so the code can no longer be looked up by value — verification fetches the user's one outstanding token and checks against it.
- **A five-guess budget**, which is what makes the 30-minute window safe. The TTL was raised from 15 minutes because a failed send is now re-driven only by the 10-minute cron sweep, so a transient Brevo blip could otherwise deliver a code that was already dead. A longer window is also a longer guessing window; five guesses per issued code closes that.
- **`create_password_reset_token` returns the plaintext, never stores it.** That return value is the only moment it exists. There is no way to recover a code afterwards — for us or for anyone reading the table.

**Rate limiting** with a correctly-implemented client IP:

```
RATE_LIMITS = {
    # Burst — "how fast"
    "auth_login":                   "10/m",  keyed on IP
    "auth_login_account":           "10/h",  keyed on the submitted email
    "token_refresh":                "30/m",  keyed on IP
    "password_reset_request":        "3/h",  keyed on (IP, email)
    "password_reset_verify":        "10/m",  keyed on IP
    "password_reset_confirm":       "10/m",  keyed on IP
    # Daily backstops — "how much in a marathon". Each anonymous endpoint owns
    # its own day; the shared DRF `anon` ceiling is only a safety net.
    "auth_login_daily":            "100/d",  keyed on IP
    "auth_login_account_daily":     "50/d",  keyed on the submitted email
    "token_refresh_daily":         "500/d",  keyed on IP
    "password_reset_request_daily": "20/d",  keyed on IP
    "password_reset_verify_daily":  "50/d",  keyed on IP
    "password_reset_confirm_daily": "20/d",  keyed on IP
}
```

The per-minute tiers cap a **burst** and nothing else — 10/m sustained is 14,400
login attempts a day — so every anonymous endpoint carries its own daily cap.
They are per-endpoint rather than shared because the shared ceiling meant a
morning of failed logins from an office could refuse someone else's password
reset. Full guide: [`docs/RATE_LIMITING_GUIDE.md`](docs/RATE_LIMITING_GUIDE.md).

**Login counts failed attempts only**, and is the one limited endpoint not
wrapped at the URL. The decorator increments before the view knows the outcome,
so a *correct* sign-in used to spend anti-brute-force budget — and behind NAT one
IP is not one person, so a dozen staff arriving together could exhaust a burst
while doing nothing wrong. `apps/accounts/login_guard.py` checks on the way in
and counts on the way out.

Behind that sits an **account lock**: five consecutive failures and the account is
refused *before its password is checked* (verifying first would leave guessing
unbounded), with the password-reset flow as the recovery path — its code reaches
an inbox an attacker cannot read. Any successful sign-in resets the run, so only
an unbroken one escalates. A second counter keyed on the submitted email keeps
the response identical for addresses with no account, so the lock is not a
user-enumeration oracle. Staff release a lock with the **Release login lock**
admin action, which clears the stored counter *and* both account-keyed cache
buckets together — clearing only the database counter would look like it worked
and then hand the user a 429 on their next attempt.
See [`docs/adr/0002-login-failure-tracking.md`](docs/adr/0002-login-failure-tracking.md).

`client_ip` uses the **rightmost-untrusted** algorithm rather than naively trusting `X-Forwarded-For`: `resolve_client_ip` walks XFF right-to-left and returns the first entry that isn't inside the trusted-proxy CIDR set (RFC-1918 + loopback). If `REMOTE_ADDR` is not in that set the edge proxy was bypassed and the header is fully attacker-controlled — so it's discarded and the connecting address is used. Deliberately not DRF's `NUM_PROXIES` ("take the Nth entry from the right"), which goes stale silently the moment a hop is added or removed; trusting by identity survives that. A spoofed header cannot move an attacker between rate-limit buckets. Password reset uses a composite `(IP, email)` key so one IP can't enumerate accounts by cycling emails, and one email can't be flooded from one IP.

Counters live in the Django cache. **`CACHE_REDIS_URL` must point at Redis in production** — with the LocMem fallback each gunicorn worker keeps independent counters and the effective limit multiplies by worker count. Redis has no other job here — there is no broker to share it with — but keep it on its own DB index on a shared instance.

**Object-level authorization** — `can_access_event` / `can_access_portal` / `can_access_portal_resource`, with `enforce()` raising `PermissionDenied` so a view reads as one guard line. Staff see anything; a client sees only their own. `get_portal()` has two modes: `?portal_id=` is a staff lookup, absent means derive from `request.user` — so a client can never reach another portal even knowing its UUID.

**UUID primary keys** for everything exposed in a URL, so ids are never enumerable.

**Offboarding is a reversible state, not a delete** — one symmetric `PATCH /users/<email>/status/` endpoint (`is_active: false` offboards, `true` restores), routed through the same service functions the admin's bulk actions use so the two can't diverge. Users are FK targets of their portal, events, and documents; deleting one would take all of it.

**Destructive operations are previewed** — `GET /event/<slug>/delete-impact/` reports the cascade, and `DELETE` refuses with `confirmation_required` unless `?confirm=true` when anything is attached.

**One error envelope, everywhere** — `{detail, code, errors?}` on every error response project-wide, from `enforce()`, from `get_object_or_404`, from hand-built error responses, and from unhandled 500s (`code=internal_error`). **The frontend switches on `code`, never on `detail`** — prose changes, codes are stable.

**Fail-fast configuration.** `DJANGO_SECRET_KEY`, `DATABASE_URL`, `BREVO_API_KEY`, all thirteen `BREVO_TEMPLATE_*` ids, `CORS_ALLOWED_ORIGINS`, and `FRONTEND_BASE_URL` raise `ImproperlyConfigured` **at boot** if missing, and so does `CACHE_REDIS_URL` whenever `DEBUG=False`. `FRONTEND_BASE_URL` in particular has no code default on purpose — a default would let a misconfigured deploy quietly mail users a login link to someone else's environment instead of refusing to start.

### 13. Observability that feeds a real monitoring stack

Built to a written, codebase-agnostic contract (`docs/OBSERVABILITY_STANDARD.md`) with one governing principle: **the app emits signals; the monitoring stack decides alerts.** No Telegram or ntfy call is ever hardcoded in application code — alerting policy belongs in Grafana and GlitchTip.

| Concern | Implementation |
|---|---|
| **Correlation** | `X-Request-ID` per request (inbound or generated), held in a `ContextVar`, echoed on the response, and **carried into background threads** — a `ContextVar` is not inherited by a new thread, so dispatch copies the caller's context explicitly. A request and every job it dispatches share one id |
| **Structured logs** | One JSON object per line → Loki/Grafana, with stable `timestamp, level, logger, message, request_id, user_id`. Console format allowed in local dev only |
| **Secret scrubbing** | A shared `scrub()` redacts sensitive keys on **both** the log path and Sentry's `before_send` |
| **Errors + traces** | Sentry SDK pointed at self-hosted GlitchTip, `send_default_pii=False`, Django + Redis integrations. A background task that raises is caught, logged with `exc_info` and reported — a bare thread that raises otherwise vanishes without a trace, which would be strictly worse than the broker it replaced |
| **Event taxonomy** | A reserved `event="<slug>"` key on notable log lines (`brevo_outage`, `brevo_recovered`, `brevo_send_failed`, `brevo_send_deferred`, `notifications_purged`, `event_details_dispatched`, `reset_tokens_pruned`, `reset_code_rejected`, `unknown_timezone`, `inquiry_no_recipients` — the last fires when a lead saves but no staff member is flagged to be told about it). **Alert rules match the label, never message text** |
| **Health** | `GET /health/` (liveness, no I/O, no auth) and `GET /health/ready/` (checks DB + cache, 503 on failure so a proxy withholds traffic) — plain Django views, no DRF, mounted **outside** the API version prefix |

**Every sink is env-gated.** An unset DSN or Loki URL means that handler is never even constructed — local, CI, and test stay silent with zero config.

### 14. Configurable without a redeploy

A recurring theme, and a deliberate one: anything a non-engineer might reasonably want to change lives in the database, not in env vars.

| Setting | Where | Was previously |
|---|---|---|
| Notification on/off, **per type** (13) | `Notification Type Settings` | — |
| Background task on/off, **per task** (9) | `Scheduled Task Settings` | — |
| A client's calendar timezone | `User.timezone`, or the client themselves via `PATCH /users/me/update/` | *(nothing — everything was UTC)* |
| Phase auto-lock: master switch, trigger phase, which locks | `Portal Settings` | — |
| Event-details email debounce window | `Portal Settings` | `EVENT_DETAILS_NOTIFY_DEBOUNCE_SECONDS` env var |
| "Contact Your Team" email + WhatsApp | `Portal Settings` | `HEPHZIBAH_CONTACT` env var |
| Portal template documents + welcome message | `Portal Defaults` (singleton) | — |
| Default "Meet Your Team" members | `TeamMember.is_default` | manual per-client assignment |

One thing left this table when Celery did, and it is worth being straight about: **task *timing* is no longer admin-editable.** It used to be a `PeriodicTask` crontab row you could edit in the Django admin with no redeploy. It is now each cron service's Cron Schedule — still no redeploy, but a different dashboard, and a genuine loss. The on/off switch above is unaffected: every scheduled task still checks `ScheduledTaskSettings.is_task_enabled()` as its first statement, whether it is invoked by cron or dispatched into the thread pool.


### 15. No Celery — in-process work plus platform cron

Three always-on services ran from this repo, and two of them existed to service **seven tasks**, of which exactly **one** genuinely needed to leave the request path (a Brevo API call) and one was a delayed job. The other five were pure cron: they needed a scheduler, not a broker.

What that cost, with every cheap mitigation already applied (`CELERY_TASK_IGNORE_RESULT`, remote control off, task events off, `--without-gossip --without-mingle --without-heartbeat`):

- **An idle `BRPOP` floor.** kombu's Redis transport blocks with a 1-second timeout, so a completely idle worker still issued ~86,400 commands/day with nothing enqueued, plus `LLEN` and QoS bookkeeping.
- **Beat pinned Postgres awake.** `DatabaseScheduler` polled `django_celery_beat_periodictasks` every ~5 seconds on a persistent connection — enough on its own to stop a serverless Postgres scaling to zero, *even with every periodic task disabled*. The documented remedy was "scale the beat service to 0 by hand": a manual workaround for a structural problem.
- **Two idle services billing RAM and CPU 24/7.**

Both stores punish polling, so **nothing polls now.**

**Deferred work is pushed into a bounded thread pool inside the web process** (`apps/core/background.py`) — a process that is already running, already paid for, and deliberately kept awake. Five details in that module are load-bearing rather than incidental:

1. **`connections.close_all()` in a `finally`.** Django connections are thread-local, and a leaked one holds the serverless Postgres awake — trading the Redis bill for the Neon bill this migration exists to avoid.
2. **Dispatch via `transaction.on_commit`**, so a thread can never observe a row its own transaction has not committed. Several `queue_notification` callers run inside `transaction.atomic()`; this race existed under Celery too, and the broker's network hop merely tended to lose it for us.
3. **Async is opt-in per process, and only `config/wsgi.py` opts in.** Everywhere else `.delay()` runs inline. Not a nicety: the retry sweep dispatches sends from inside a cron process that exits seconds later, and a pool there would silently drop exactly the mail the sweep exists to rescue.
4. **`contextvars` copied into the worker thread**, or every background log line loses the `X-Request-ID` correlation the observability standard depends on. This replaced three Celery signal handlers that carried the id across the broker as a task header.
5. **Catch broadly, log, report.** A thread that raises disappears without a trace — strictly worse than the broker it replaced.

Backpressure degrades to **inline, never dropped**: slower, never lossy. That path is real here, not theoretical — the inquiry endpoint fans out one notification per flagged staff member, on a public unauthenticated route.

**Scheduling is platform cron** — `manage.py run_scheduled <group>`, three services that run and exit, billing execution time rather than 24/7.

#### The invariant

> **Every deferred task must have a durable status field and a cron sweep that re-drives it. A task that can only be triggered once is a task that will be lost.**

This is not advice; it is what makes the trade safe. The pool has no persistence and no delivery guarantee. `Notification.status` already satisfied it; the event-details debounce did not, and was redesigned ([§5](#5-trailing-debounce--one-email-per-editing-session-not-one-per-save)).

#### It found four bugs that existed *under* Celery

Worth stating plainly, because they are the actual return on this work — not the hosting bill:

- **Stranded `queued` notifications.** The sweep scanned `FAILED` only. A row created and committed, whose dispatch was then lost to a deploy, sat `queued` for ever with nothing looking at it. The broker mostly plugged this; removing it made the hole primary, so the sweep now re-drives `queued` rows older than 10 minutes.
- **A half-durable debounce.** The token was a column; the schedule was a broker message. See [§5](#5-trailing-debounce--one-email-per-editing-session-not-one-per-save).
- **Duplicate digest emails.** Both digests queued the mail *then* wrote the marker, so a failure in between re-sent the next day. Reversed.
- **A breaker that could mute the platform.** `down` is cleared only by a successful send, and while it is set every ordinary send parks itself — so a stale row parked everything, indefinitely. Now capped at 30 minutes.

And a set unrelated to Celery, which the same study surfaced:

- **Temporary passwords and reset codes sat in `Notification.context` in plaintext** — 90 days for a successful send, for ever for a failed one, readable in the admin and in every database backup. Redacted on terminal states, excluded from the admin, backfilled by migration. The most severe finding.
- **Reset codes sat in `PasswordResetToken.code` in plaintext too**, and the admin printed them in a column. Now PBKDF2-hashed, with a five-guess budget per code — see [§12](#12-security-and-access-control).
- **The daily digests measured their lookahead in UTC**, against `DateField`s that mean a calendar day where the *client* lives. Off by one for anyone far enough from UTC, so a three-day payment reminder fired two or four days out. Now resolved per recipient (`apps/core/timezones.py`) — the fix a worldwide client base needs.
- **`status=failed` was used for mail that was never attempted.** A row the Brevo breaker parked showed as a failure with `attempt_count=0`. It has its own `deferred` status now; the sweep treats them identically, so this is purely about not lying to staff.
- **The Brevo SDK client was rebuilt on every send** — a fresh TLS handshake per email, and a connection pool that was never reused. Built once now, sized to the background pool.
- **Four maintenance tasks that had never run at all**, each closing a table or bucket that only grew (see the list above).

#### What it cost

| Loss | Mitigation |
|---|---|
| In-flight work dies on restart | The durable row plus its sweep. This is the central trade |
| Admin-editable task *timing* | Moved to each cron service's Cron Schedule — still no redeploy, a different dashboard. The on/off switch is untouched |
| Exact-second scheduling | Only affected the debounce; now quantised to the sweep cadence, and durable in exchange |
| Detecting a Brevo outage before the first failed send | Accepted: threshold-3 passive detection plus a staleness ceiling, against 576 HTTPS calls/day |

The reasoning, the rejected alternatives (a Postgres-backed queue, `LISTEN/NOTIFY`, QStash, APScheduler, an authenticated cron endpoint) and the rollback are recorded in [`docs/adr/0001-remove-celery.md`](docs/adr/0001-remove-celery.md).


---

## Use cases

| Setting | What this platform gives them |
|---|---|
| **Luxury wedding planning** | Bride/groom-derived titles and reference codes, per-day contact lists (traditional vs. white wedding vs. reception), aso-ebi and decor conversation threads, a signable service agreement with a 30/40/30 payment schedule |
| **Milestone birthdays & private celebrations** | Honoree-driven event identity, a curated VIP and family contact book, phase-gated prep checklists, budget tracking by vendor category |
| **Corporate events & brand activations** | Multi-day event structure with per-day venues and guest counts, decision-maker and approvals contact category, invoice/receipt trail with auto-numbered references |
| **Repeat and multi-event clients** | One permanent portal, an independent engagement per event, complete history preserved and correctly attributed — no duplicate accounts, no overwritten past |
| **Planning agencies with staff teams** | Staff-only writes across every workflow, per-client team assignment with auto-seeded defaults, attribution on every phase and detail change, reversible offboarding |
| **Client-facing transparency** | A portal showing exactly the client's own slice — their phase, their documents, their money, their prep — enforced at the object level, not by UI hiding |

---

## Architecture

```
   Client portal   ───▶ ┌─────────────────────────────────────────┐
   (JWT, own data only) │        Django 5.2 / DRF · services      │
   Staff dashboard ───▶ │  portal · events · meetings · contacts  │
   Django admin    ───▶ │  conversations · reminders · budgets ·  │
                        │  document_hub · documents · accounts ·  │
   Public form     ───▶ │  inquiries · notifications              │
                        └───┬─────────────────────────┬───────────┘
                            │                         │
              ┌─────────────▼──────────┐  ┌───────────▼────────────┐
              │      PostgreSQL        │  │  Redis                 │
              │  portals · engagements │  │  cache · rate limits    │
              │  events · documents ·  │  │  inquiry dedupe        │
              │  payments · audit      │  │  (no broker)           │
              │  notification status ──┼──┤                        │
              └───────────┬────────────┘  └────────────────────────┘
                          │  ▲
                          │  │ re-drives anything stranded
                          │  │
                          │  └──────────────────────────────────┐
                          │                                     │
        in the SAME web process:                    platform cron (3 services,
        ┌─────────────────▼──────────────┐          billed per run, not 24/7):
        │  bounded thread pool (4)       │          ┌──────────────────────────┐
        │  dispatched on_commit          │          │ */10  notification_retry │
        │  notify · debounce · digests   │          │ 08:00 daily_maintenance  │
        └──────┬──────────────┬──────────┘          │ Mon03 weekly_maintenance │
               │              │                     └───────────┬──────────────┘
               │              │                                 │
   ┌───────────▼──────────┐ ┌─▼────────────────┐                │
   │  Cloudflare R2       │ │  Brevo           │◀───────────────┘
   │  signed docs         │ │  13 templates    │
   │  public images       │ └──────────────────┘
   └──────────────────────┘   GlitchTip · Loki/Grafana
```

**The rule that holds this up:** *every deferred task has a durable status field and
a cron sweep that re-drives it.* The pool has no persistence — if the process dies,
in-flight work is gone — so the row is written and committed first, and the sweep is
what makes that safe. See [§15](#15-no-celery--in-process-work-plus-platform-cron).

### Apps

| App | Responsibility |
|---|---|
| `core` | Base models (UUID PK, timestamps), permission vocabulary + `enforce()`, error envelope and codes, deep-link registry, rate-limit key callables, pagination strategies, storage policy, structured logging, Sentry, health probes. Owns no tables, exposes no endpoints. |
| `accounts` | Custom user, JWT, admin-driven registration, forced password change, 3-phase password reset, staff user directory, reversible offboarding |
| `portal` | `ClientPortal` + `EventEngagement`, the six-phase journey, phase attribution and auto-lock, team members and assignment, `PortalSettings` |
| `events` | `Event` + `EventDay`, frozen slugs, type-conditional required fields, the aggregate detail endpoint, the debounced update notification |
| `meetings` | Meetings, status state machine, `.ics` generation, prep items/fields/responses/uploads, derived completion, prep-due digest |
| `contacts` | Per-event curated address book, five categories, day-pinning with a copy-from-day action, contacts lock |
| `conversations` | Threads logging off-platform communication (WhatsApp/phone/email), validated tag vocabulary, deep-link pills |
| `reminders` | Staff-authored client to-dos, priority weighting, object-targeted deep links — the reference implementation of the project's conventions |
| `documents` | Generic-FK catalog of every file produced as a side effect. One read route, no writes |
| `document_hub` | Client-facing documents, payment schedule + milestones, invoices, receipts, auto-seeded defaults, reference-code generation |
| `budgets` | Per-event budget, category allocation with variance, payment history with receipts |
| `notifications` | The dispatch pipeline: `queue_notification`, Brevo integration, the retry/stranded sweep, per-type and per-task toggles, the circuit breaker, auth-secret redaction, read-only history |
| `inquiries` | Pre-relationship lead capture: the project's one public, unauthenticated write, rate-limited and dedupe-guarded, plus a staff-only triage API. Client acknowledgement + internal lead alert emails; DB-level date-range constraint |

### Scheduled work (platform cron)

`python manage.py run_scheduled <group>` runs one group to completion and exits. Three cron services, grouped by cadence rather than one entry per job:

| Group | Cadence | Tasks |
|---|---|---|
| `notification_retry` | `*/10 * * * *` | Re-drive failed **and stranded-`queued`** notifications; send debounced event-details emails whose window has closed |
| `daily_maintenance` | `0 8 * * *` | Payment-due digest; meeting-prep digest; prune expired reset tokens; flush expired JWTs; clear expired sessions |
| `weekly_maintenance` | `0 3 * * 1` | Purge terminal notifications older than 90 days; sweep orphaned documents and R2 blobs |

A failing task doesn't strand the rest of its group; the command reports every failure and exits non-zero, which is what the platform's own run history alerts on.

`notification_retry` gets its own group and its own short cadence because it is the **only** retry path for a failed email, and a password-reset code lives 30 minutes. Widening it to save Postgres wake-ups is a real trade — against password-reset recovery — and the TTL has to move with it.

The two digests used to be separate jobs 15 minutes apart; that stagger only existed so two beat jobs wouldn't fire simultaneously, and running them sequentially in one process is strictly simpler.

Digests are periodic rather than event-driven because they're **genuinely time-relative** — "this becomes relevant N days before X" has no single triggering moment, unlike a new reminder. Each scan stamps the source row (`PaymentMilestone.reminder_sent_at`, `Meeting.prep_reminder_sent_at`) **before** queueing the email, so a re-run finds the marker set and skips — and so a failure can never leave the mail sent and the marker unwritten.

---

## API surface

Base path **`/api/v1/`**. Admin at `/admin/`, health at `/health/` and `/health/ready/` — both outside the version prefix. Bump to `/api/v2/` as a **parallel mount** when a breaking revision is needed; never mutate v1 in place.

Path parameters are **UUIDs** (or event slugs). Reads scope to the caller: a client sees their own portal implicitly, staff pass `?portal_id=<uuid>`.

**Auth & accounts**
```
POST   auth/token/  auth/token/refresh/  auth/token/logout/          [rate-limited]
POST   auth/password-reset/request/  .../verify/  .../confirm/       [rate-limited]
POST   auth/force-password-change/
GET    users/                          Staff directory — role, is_active, search, allow-listed ordering
GET    users/me/   PATCH users/me/update/   GET users/<email>/
POST   users/register/                 Staff-only account creation (temp password emailed)
PATCH  users/<email>/status/           Reversible offboard / restore
```

**Inquiries** — the one place an anonymous caller creates a record
```
POST   inquiries/                      [public] [rate-limited] Lead capture. Fixed 201, no id echoed back
GET    inquiries/                      Staff-only list — ?status= ?event_type= ?search=, allow-listed ?ordering=, opt-in ?page=
GET    inquiries/<uuid>/               Staff-only detail
PATCH  inquiries/<uuid>/status/        Staff-only triage: new · contacted · qualified · converted · lost · archived
```

**Portal**
```
GET    portal/                         Overview: phase, team, contact, locks, welcome message
PATCH  portal/update/                  Welcome message, active event
PATCH  portal/phase/                   Set or advance phase (attributed; applies auto-lock)
PATCH  portal/activate-event/          Switch the active engagement
GET    portal/team/  POST portal/team/assign/  DELETE portal/team/remove/
CRUD   portal/team-members/…           Global team member profiles (+ is_default)
```

**Events**
```
POST   event/create/                                Title derived from type-conditional name fields
GET    event/<slug>/                                Single event
GET    event/<slug>/detail/                         Aggregate: event + days + grouped contacts + planning stage
GET    event/all   ·   event/all/user/<email>/
PATCH  event/update/<slug>/                         Attributed; schedules the debounced client email
GET    event/<slug>/delete-impact/                  Cascade preview
DELETE event/delete/<slug>/                         Refuses without ?confirm=true when data is attached
PATCH  event/details-lock/                          Toggle client edit lock
CRUD   event/<slug>/event_day/…                     Sub-days: venue, times, guest count, booking status
```

**Contacts · Conversations · Reminders**
```
GET    event/<slug>/contacts/          Grouped by category
GET    event/<slug>/contacts/summary/  Per-category counts
POST   event/<slug>/contacts/copy/     "Same as" — duplicate a day's contacts to another day
CRUD   event/<slug>/contacts/…    ·    PATCH contacts/lock/

GET    conversations/                  Defaults to 4 per query; ?limit=all to load everything
GET    conversations/tags/  ·  conversations/phases/
CRUD   conversations/…                 Validated tags + deep-link pills

GET    reminders/   ·   POST reminders/create/      Object-targeted deep links
CRUD   reminders/<uuid>/   ·   PATCH reminders/<uuid>/complete/
```

**Meetings & prep**
```
GET    meetings/   ·   meetings/phases/   ·   POST meetings/create/
CRUD   meetings/<uuid>/     ·   GET meetings/<uuid>/ics/       Calendar invite
PATCH  meetings/<uuid>/status/                                 Validated transition
POST   meetings/<uuid>/notes/
POST   meetings/<uuid>/prep/                                   Item + nested fields, atomically
CRUD   meetings/<uuid>/prep/<item>/   ·   .../fields/<field>/
POST   .../fields/<field>/respond/                             Client or staff; ?replace=true to swap files
DELETE .../fields/<field>/respond/   ·   .../uploads/<id>/
```

**Document hub & budgets**
```
GET    document-hub/                          Aggregate: documents, schedule, milestones, invoices, receipts
GET    document-hub/defaults/   PATCH …       Org-wide templates for future clients
CRUD   document-hub/documents/…               Auto-coded on create
POST   document-hub/payment-schedule/         Auto-generates 30/40/30
PATCH  document-hub/payment-schedule/<uuid>/  Re-splits milestones on total change
POST   .../milestones/                        Ad-hoc, fixed-amount, outside the sum-to-100 rule
PATCH  document-hub/milestones/<uuid>/mark-paid/
CRUD   document-hub/invoices/…   ·   document-hub/receipts/…

GET    event/<slug>/budget/                   Tiles: allocated, spent, remaining, health %, status
CRUD   event/<slug>/budget/categories/…       Variance: positive = over budget
GET    event/<slug>/budget/payments/          Paginated history
CRUD   event/<slug>/budget/payments/…         Receipt upload → registered in the document catalog
```

**Read-only**
```
GET    documents/          Every file across the engagement (generic-FK catalog)
GET    notifications/      Email delivery history
```

---

## Getting started

### Requirements

Python 3.11 · PostgreSQL · Redis *(optional in dev — LocMem fallback; required when `DEBUG=False`)*

### Local

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env                                 # fill in the REQUIRED keys
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

**That's it — there are no extra processes to run.** `runserver` boots through
`config/wsgi.py`, so deferred work runs in its thread pool exactly as it does in
production. To run a scheduled group by hand:

```bash
python manage.py run_scheduled --list               # the groups and what's in each
python manage.py run_scheduled notification_retry
```

Set `BACKGROUND_EAGER=True` to force every `.delay()` inline and debug a task
synchronously inside the request that triggered it.

**The app will refuse to boot with a missing required key** — that's intentional. [`.env.example`](.env.example) documents every variable with a `(required)` / `(optional …)` marker.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | — | **Required** |
| `DATABASE_URL` | — | **Required.** Postgres DSN |
| `CACHE_REDIS_URL` | *(LocMem)* | Django cache, rate-limit counters, inquiry dedupe, `/health/ready/`. **Required when `DEBUG=False`** — LocMem is per-process, so with `gunicorn --workers 3` every limit is silently 3× its configured value |
| `BACKGROUND_MAX_WORKERS` | `4` | Threads available for deferred work |
| `BACKGROUND_MAX_QUEUED` | `100` | Jobs queued-or-running before dispatch degrades to inline. Slower, never lossy |
| `BACKGROUND_EAGER` | `False` | Force every `.delay()` inline, even in the web process. Forced `True` under the test runner |
| `PLATFORM_DEFAULT_TIMEZONE` | `UTC` | Calendar timezone for accounts that haven't set their own. **Not** `TIME_ZONE` — that stays UTC and governs instants. This decides which *day* a client's payment-due and meeting-prep digests are measured against. Validated at boot |
| `FRONTEND_BASE_URL` | — | **Required.** Portal origin; every email link is built from it. No code default, on purpose |
| `CORS_ALLOWED_ORIGINS` | — | **Required** |
| `BREVO_API_KEY` | — | **Required.** All outbound mail |
| `BREVO_TEMPLATE_*` (×13) | — | **Required.** One numeric Brevo template id per notification type |
| `ALLOWED_HOSTS` | `[]` | Required once `DEBUG=False` |
| `CSRF_TRUSTED_ORIGINS` | each host as `https://` | Override to add e.g. a `www` host |
| `USE_R2_STORAGE` | `False` | On ⇒ R2; off ⇒ in-memory (tests/CI only). **There is no local-disk path** |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_ENDPOINT_URL` / `R2_BUCKET_NAME` | — | Private bucket — signed document URLs |
| `R2_PUBLIC_BUCKET_NAME` / `R2_PUBLIC_URL` | *(blank)* | Public bucket for display images. Blank ⇒ images fall back to signed URLs (safe, just 1h-lived) |
| `R2_SIGNED_URL_EXPIRE_SECONDS` | `3600` | Document URL lifetime |
| `RATE_LIMIT_*` (×6) | see §12 | Per-endpoint throttles — the five auth ones plus `RATE_LIMIT_INQUIRY_SUBMIT` (`3/h`) for the public inquiry form |
| `RECAPTCHA_SECRET_KEY` | *(blank ⇒ off)* | Optional. Verifies the public inquiry form's token against Google when set; blank skips verification entirely, so local/CI/tests need no config |
| `SECURE_HSTS_SECONDS` | `3600` | Raise to `31536000` before preload submission |
| `SECURE_SSL_REDIRECT` | `False` | Opt-in — a hard redirect can break plain-HTTP health checks |
| `LOG_FORMAT` / `LOG_LEVEL` | `console` / `INFO` | `json` for real deploys |
| `SENTRY_DSN` | *(blank ⇒ off)* | GlitchTip project DSN |
| `LOKI_PUSH_URL` + user/password | *(blank ⇒ off)* | Structured log shipping |
| `SENTRY_ENVIRONMENT` | `development` | Also the Loki `environment` label |

---

## Operating it

### Management commands

| Command | Purpose |
|---|---|
| `run_scheduled <group>` \| `--list` | Run one group of scheduled tasks to completion, then exit. **This is the whole scheduler** — platform cron invokes it. A failing task doesn't strand the rest of its group; the command exits non-zero if any failed |
| `cleanup_orphaned_documents [--dry-run]` | Sweep dangling registry rows and hub file blobs with no owning record (deleted/replaced files, rollback leftovers). Scoped so it never touches other apps' files. Also runs weekly via `run_scheduled weekly_maintenance` |

### Tests & quality

```bash
pytest                             # 271 tests across all 13 apps
pytest --cov=apps                  # with coverage
ruff check .   ·   ruff format .   # config in pyproject.toml
python manage.py check --deploy    # run with DEBUG=False
python manage.py makemigrations --check --dry-run   # migration drift
```

Background work runs **inline under test**, guaranteed twice over: `BACKGROUND_EAGER` is forced `True`, *and* async dispatch is opt-in per process and the test runner never opts in. The cache is forced to LocMem regardless of `CACHE_REDIS_URL`, so a test's `cache.clear()` can never `FLUSHDB` a real, possibly shared Redis. Rate limiting is disabled under test; the rate-limit tests opt back in explicitly.

The pool's *async* behaviour is tested too, with `captureOnCommitCallbacks(execute=True)` rather than `TransactionTestCase` — which doubles as the assertion that dispatch really goes through `on_commit`, since a callback that was never registered there cannot be captured. (`TransactionTestCase` was the first attempt and is the wrong tool: it `TRUNCATE`s tables without restoring migration-seeded data, which broke four unrelated tests in full-suite runs only.)

CI (`.github/workflows/ci.yml`) runs **blocking** `ruff check`, the migration-drift check, deploy checks, the full suite with `--cov-fail-under=77`, a from-empty `migrate` on a throwaway database, and a resolve-check on every scheduled-task group — on every push and PR.

Two of those exist because a `--reuse-db` suite cannot tell you: whether the migration chain still applies to an empty database, and whether every dotted path in `run_scheduled`'s `GROUPS` still resolves (a typo there is a task that silently never runs).

`ruff format` is deliberately not run. It would reformat 104 files, and this codebase's hand-alignment and long explanatory comments are intentional; the rules that catch real problems (`F`, `E`, `I`) are what gate. One `# noqa: F401` in the tree is load-bearing — `apps/portal/apps.py` imports `apps.portal.signals` for its side effects, and `ruff check --fix` deleted it once, silently disabling client-portal creation. Ruff cannot see side-effect imports.

### Deploy

**One always-on service and three cron services** from this one repo, sharing an environment:

```
web           collectstatic && gunicorn config.wsgi --workers 3 --threads 4 --timeout 120  (always on)
cron-notify   manage.py run_scheduled notification_retry     */10 * * * *
cron-daily    manage.py run_scheduled daily_maintenance      0 8 * * *
cron-weekly   manage.py run_scheduled weekly_maintenance     0 3 * * 1
```

A cron service runs its command on schedule and **exits**, so it bills execution time — a few minutes a day — rather than 24/7. That is where the cost of the old `worker` + `beat` pair came back. Render won't start a run while the previous one is still going, which is exactly the overlap guard the sweeps want.

Every service needs the **full** env var set: `config/settings.py` fails fast on a missing key, so a cron service missing one `BREVO_TEMPLATE_*` crash-loops just as the old worker did. `migrate` runs as the **web** service's `preDeployCommand` only — two services migrating a fresh database concurrently is a race worth not reintroducing.

Media **must** be on R2 in production — Render's disk is ephemeral, and there is no filesystem media path in this codebase to fall back to.

[`RUNBOOK.md`](RUNBOOK.md) is the operator guide: bootstrap, deploy steps, post-deploy checklist, verifying background dispatch end to end, diagnosing what's keeping the DB compute awake, and the common day-two tasks.

---

## Documentation map

Design decisions live next to the code they describe — **each app has its own `README.md` covering the *why*, not just the *what***, and that's the first place to look.

| Doc | Contents |
|---|---|
| [`RUNBOOK.md`](RUNBOOK.md) | Operator guide — bootstrap, deploy, background work and cron, troubleshooting, admin toggles |
| [`docs/adr/`](docs/adr/) | Architecture decision records. [`0001-remove-celery.md`](docs/adr/0001-remove-celery.md) covers the move to in-process work + platform cron: the measurements, the durability invariant, what it cost, the rejected alternatives, and the rollback |
| [`The Structure.md`](The%20Structure.md) | Full model relationship map |
| [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) | Cross-cutting conventions: auth, roles, pagination, the error envelope. **Not** a route list — each app's `urls.py` is the routing source of truth, with a method comment on every `path()` |
| [`docs/OBSERVABILITY_STANDARD.md`](docs/OBSERVABILITY_STANDARD.md) | The telemetry contract, written to be portable across repos |
| [`docs/FAILURE_POINTS_AUDIT.md`](docs/FAILURE_POINTS_AUDIT.md) | Enumerated failure modes and their resolutions |
| [`docs/brevo-templates/`](docs/brevo-templates/) | The expected merge params for each of the 13 email templates |
| `apps/*/README.md` | Per-app behavioral design — the reasoning behind each workflow |
