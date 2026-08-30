# Inquiries App

The `inquiries` app captures **prospective-client enquiries** — the "get in
touch / plan my event" form a lead fills in before they become a client with a
portal. It is deliberately separate from the client-facing portal apps: an
inquiry is a lead record the Hephzibah Luxe team triages, not something tied to
an `EventEngagement`.

It owns **the one public write path in the project**: `POST /api/v1/inquiries/`
is unauthenticated, so everything else in this app exists to make that safe —
two rate-limit tiers at the URL, an optional reCAPTCHA, a double-submit dedupe window,
and a response body that echoes nothing back. Staff read and triage leads
through a small authenticated API and the Django admin.

---

## Models

### `InquiryForm`
One submitted enquiry. Inherits `core.UUIDTimestampedModel`, so it carries a
**UUID primary key** and `created_at` / `updated_at` — the PK is what the staff
routes put in a URL, and it is never a sequential integer.

* **first_name** / **last_name**: the lead's name.
* **email** / **phone_number**: contact details.
* **contact_mode**: preferred channel — `Email` or `Phone Number` (optional).
* **event_type**: `Birthday`, `Wedding`, `Corporate`, `Social Events`, or
  `Others` (optional). Uses `Event.EVENT_TYPE` directly, so an accepted lead
  maps cleanly onto a real event later.
* **preferred_start_date** / **preferred_end_date**: the desired event window.
* **desired_location**: free-text location/venue preference.
* **budget**: optional `Decimal` budget indication, `max_digits=14` — a ceiling
  of ₦999,999,999,999.99, so no realistic luxury budget is rejected by the
  column. `null` is the "not sure yet" answer; a sentinel `0`/`-1` was rejected
  because it would silently poison any future budget reporting.
* **details**: free-text notes about the enquiry.
* **status**: triage state — `new` (default) → `contacted` → `qualified` →
  `converted`, plus `lost` / `archived`. Staff-settable only; it is not part of
  the public submit payload.
* **created_at** / **updated_at**: arrival time and last triage edit, from the
  base model. Default ordering is `-created_at` — **newest lead first**, not
  soonest event, so a 2028 wedding doesn't outrank an inquiry from this morning.

**Date-range integrity is enforced at the database level.** A
`CheckConstraint` (`valid_preferred_date_range`) guarantees
`preferred_end_date >= preferred_start_date`, so an inverted window can never be
stored — regardless of how the row is created (admin, shell, or the public
endpoint). Note the SQL three-valued-logic hole: `NULL >= x` is `NULL`, not
false, so the constraint no-ops when either date is missing. The API closes that
by requiring both dates in the serializer; an admin/shell write can still store
a half-filled range.

`__str__` renders as `"<first> <last> - <event_type or 'Event Inquiry'> @ <location>"`
for readable rows in admin lists.

---

## The public endpoint

```
POST /api/v1/inquiries/          unauthenticated · rate-limited · JSON only
```

Mounted through `config/urls.py` like every other app (prefix-free internally,
`/api/v1/` applied once). The trailing slash is mandatory — a slashless POST is
not redirected.

**Request** — every field is required except the two noted:

```json
{
  "first_name": "Ada",
  "last_name": "Okoye",
  "email": "ada@example.com",
  "phone_number": "+2348012345678",
  "contact_mode": "Email",
  "event_type": "Wedding",
  "preferred_start_date": "2027-06-01",
  "preferred_end_date": "2027-06-03",
  "desired_location": "Lagos, Nigeria",
  "budget": "45000000.00",
  "details": "Expecting 250 guests, full planning and design.",
  "recaptcha_token": "03AGdBq26…"
}
```

- **`budget`** — nullable, backing the "not sure yet" option. Send `null` or omit.
- **`recaptcha_token`** — write-only, never a model field, never persisted.
  Only checked when `RECAPTCHA_SECRET_KEY` is configured.
- The choice fields take the **stored values, not the display labels** —
  `"Birthday"` (whose label is "Birthday Party"), `"Corporate"` (label
  "Corporate Event"), `"Wedding"`, `"Social Events"`, `"Others"`; and `"Email"` /
  `"Phone Number"`.

**Responses:**

- **201** — `{"detail": "Your inquiry has been received. We'll be in touch within 2 business days."}`.
  Deliberately **no `id` and no echo of the saved row**: this is an
  unauthenticated endpoint, and reflecting stored data back would hand an
  attacker a confirmation oracle.
- **400** — the standard `{detail, code: "validation_error", errors: {...}}`
  envelope. Raised for a missing required field, a choice value outside the
  vocabulary, `preferred_end_date` before `preferred_start_date`, a
  `preferred_start_date` before today (today itself is valid, compared with
  `timezone.localdate()` so there is no UTC off-by-one), and a failed captcha.
- **429** — `{detail, code: "rate_limited"}`, rendered by
  `apps.core.views.ratelimited`.

**Why the serializer pins fields required.** Six model fields are nullable at
the database level (`contact_mode`, `event_type`, `preferred_start_date`,
`preferred_end_date`, `budget`, `details`) but the live form asks for all of
them. A plain `ModelSerializer` would inherit `required=False` and silently
store a half-filled lead, so each is pinned `required=True` **and**
`allow_null=False` — `required` alone only rejects an *absent* key, so
`"details": null` would still get through. The model stays permissive for
admin/shell entry; the API does not. The date-order check is also duplicated in
the serializer on purpose: without it an inverted range reaches the DB
constraint and surfaces as a **500 `internal_error`** instead of a 400.

### Rate limit, dedupe and captcha

| Protection | Where | Behaviour |
|---|---|---|
| Rate limit — burst tier | `urls.py`, **outermost** around the view | `RATE_LIMITS["inquiry_submit_burst"]`, default `6/10m` (`RATE_LIMIT_INQUIRY_SUBMIT_BURST`). Its rate is a **callable** (`dedupe.burst_rate`), not a string: for a submission already accepted inside the dedupe window it returns `None`, which makes django-ratelimit skip the check *without incrementing*. That is what stops a **sequential** repeat costing a second attempt. It is not a guarantee: the marker is written at the end of the view, after the reCAPTCHA round-trip and the insert, so a genuine double-click firing ~100-300ms apart can reach the rate callable before the marker exists and still be counted. The count of 6 is sized to work either way — three submissions if every double-click costs two, six if none do. Only this tier skips; the flood tier counts every request, so replaying one payload forever is still bounded, keyed on `apps.core.ratelimit.ip_and_email` — the `(IP, email)` composite. This is the tier a real person meets when they submit again because nothing visibly happened. Its window is minutes, not an hour, so a corrected resubmit isn't stranded until the next hour boundary |
| Rate limit — flood tier | `urls.py`, inside the burst tier | `RATE_LIMITS["inquiry_submit_ip"]`, default `10/h` (`RATE_LIMIT_INQUIRY_SUBMIT_IP`), keyed on `client_ip` — the **IP alone**. This is the load-bearing half. A `(IP, email)` limit alone looks strict and isn't: the email on a public form is chosen by whoever is posting and costs them nothing, so varying it bought a fresh bucket every time and one machine could submit without limit |
| Staff lead list size | `views.list_inquiries` | `InquiryPageNumberPagination`, **10 per page**, always on (never opt-in). Same envelope as the rest of the portal; its own subclass so the lead inbox can be resized without moving Budget Payment History. A lead row carries a name, email, phone number and budget, and a rate limit bounds request *count*, not response *size* — so the page is what bounds the exposure |
| Project-wide anon ceiling | **deliberately not applied** | `submit_inquiry` carries `@throttle_classes([])`. DRF's anon throttle keys on the client only — the view is not part of its cache key — so every unauthenticated endpoint drew from one shared per-IP pool, and a burst of failed logins could leave a genuine lead unable to submit at all. This is the only endpoint in the project that opts out |
| Double-submit dedupe | `services.create_inquiry` | `DEDUPE_WINDOW_SECONDS = 120`, an atomic `cache.add()` on a namespaced SHA-256 of the **whole submission** — every validated field, `sort_keys`'d, `None`s dropped, `Decimal`s quantised to 2dp and dates ISO-formatted. An **identical** repeat inside the window returns the **same 201** with no second row and no second pair of emails; a submission differing in *any* field is its own lead |
| reCAPTCHA | `recaptcha.verify_recaptcha` | Entirely env-gated. With no `RECAPTCHA_SECRET_KEY` it is a no-op that returns `True`, so local dev, CI and tests need no config. Short timeout, and it **fails open** on any network error — losing a real lead to a Google outage is worse than accepting a spam one |

Four things about that table are load-bearing:

- **The limiter must stay outermost at the URL.** Inside DRF's dispatch the
  `Ratelimited` exception is converted to a **403**, not the 429 envelope.
- **`ip_and_email` parses `request.body` as JSON**, which is why this endpoint
  is JSON-only: a `multipart/form-data` post silently degrades to IP-only
  bucketing.
- **`CACHE_REDIS_URL` must point at Redis in production**, or each gunicorn
  worker keeps its own counters and both the limit and the dedupe window
  multiply by worker count.
- **The dedupe key fingerprints the whole payload, not the email.** It used to
  hash the email alone, which made it an *email lockout*: a lead who resubmitted
  40 seconds later with a corrected date got the same 201 and no row, and the
  correction was destroyed silently. The failure directions are asymmetric — a
  duplicate row costs staff one email they can see and delete, a lost lead is
  gone — so the key errs strict. Nothing is given up on the case the window
  exists for: a real double-click resubmits identical form state and still lands
  on one fingerprint. Dedupe is now **idempotency**; flooding stays the rate
  limiter's job.

**Known v1 defect:** the limiter counts a double-click as two requests (it
increments before the view runs) while the dedupe window collapses those same
two clicks into one lead. One fumbled double-click therefore burns two of three
hourly attempts — and since the window no longer swallows a *corrected* resubmit,
that correction now writes its lead and also burns an attempt. Shipped knowingly;
the reasoning and the costed fixes are `docs/INQUIRY_V2_BACKLOG.md` §7. The
`inquiry_dedupe_hit` log line emitted on every swallowed submit is the signal to
retune it against.

---

## The staff surface

All three routes require `IsAuthenticated, IsStaffOrSuperuser` — a client gets a
**403**. There is deliberately **no public read, no update of submitted fields,
and no delete**: what the lead typed is immutable, only triage state changes,
and leads are business records.

```
GET   /api/v1/inquiries/                List.  ?status= ?event_type= ?search= ?ordering= ?page=
GET   /api/v1/inquiries/summary/        Per-status counts. ?event_type= ?search=
GET   /api/v1/inquiries/<uuid>/         One lead, every stored field
PATCH /api/v1/inquiries/<uuid>/status/  { "status": "contacted" } — the only writable field
```

All three read through `InquirySerializer`, which is entirely read-only and
pairs each choice field with a `_display` label (`contact_mode_display`,
`event_type_display`, `status_display`) so the frontend never hardcodes the
value→label map. `GET /inquiries/` and `POST /inquiries/` share one URL
pattern and are dispatched by method in `urls.py` — two `path()` entries with
the same pattern would leave the second dead, since Django resolves on the
path alone. Only the POST branch carries the rate-limit wrapper.

Reuse rather than new design, in every case:

- **The list mirrors `GET /users/`** — the staff directory already does filter +
  search + an *allow-listed* `ordering` param. `ALLOWED_ORDERING` is
  `created_at`, `preferred_start_date`, `status`, `event_type`, `last_name`
  (prefix `-` for descending; the default is `-created_at`), and a value
  outside it is a **400**, not a silent fallback. Allow-listing matters: an
  unrestricted `order_by` lets a caller sort by any column, including ones not
  meant to be queryable.
- **Pagination comes from `apps/core/pagination.py`** and is **unconditional**,
  not opt-in. `InquiryPageNumberPagination` (10 per page, `?page_size=` up to
  50) always returns `{count, next, previous, results}`. It used to be opt-in,
  which meant the default response serialised every lead in the table — names,
  emails, phone numbers and budgets — in a single request. A rate limit caps how
  MANY requests a caller makes, not how much each one hands over, so a
  compromised staff token needed exactly one; bounding the page is the control
  that actually applies. `?page=` walks the rest, so nothing is unreachable.
  Its own paginator class rather than the shared one, whose 7 is pinned to the
  Budget Payment History Figma spec. Page-number rather than cursor, because
  `StandardCursorPagination` pins its own ordering and would fight `?ordering=`.
- **`<uuid>` is the house path converter** (`apps/meetings/urls.py`,
  `apps/reminders/urls.py`) and does the first layer of validation for free: a
  non-UUID segment never matches the route, so `/inquiries/5/` is a 404 from the
  URLconf rather than a `ValueError` inside the view.
- **Status lives on its own sub-route**, matching `PATCH portal/phase/` and
  `PATCH meetings/<uuid>/status/`, rather than a general PATCH that would let
  staff quietly rewrite a lead's submitted answers.
- **`search` covers the fields the admin already searches** — name, email,
  phone, location — so admin and API don't disagree about what "search" means.
- **`summary/` mirrors `GET /event/<slug>/contacts/summary/`** — a flat list of
  `{status, status_display, count}`, one entry per choice **including the empty
  ones**, so a dashboard's pipeline columns don't appear and disappear as leads
  move. It shares `_filtered_inquiries()` with the list so the tallies always
  agree with what the same filters would return; `?status=` is ignored there,
  since filtering a per-status tally by status answers nothing. Unlike the three
  older summary endpoints it resolves in **one** aggregate query rather than one
  `COUNT` per choice — same JSON, less work, which is the point of the endpoint.

**Triage is guarded and attributed** (backlog §2, shipped 2026-08-29).
`services.VALID_TRANSITIONS` rejects an illegal move with `invalid_transition`,
which is a different failure from a value that isn't a status at all
(`validation_error`) — the value check runs first, so a typo never reports
itself as a bad transition. Re-sending the status a lead already has is an
accepted no-op, so a frontend double-click is not a 400. `converted` is
near-terminal (its only exit is `archived`, because conversion creates a user
and an event); `lost` is revivable and `archived` restorable, so neither a
change of heart nor a mis-click is permanent.

Attribution comes from `core.models.AttributedModel`, **not** a bespoke
`status_updated_by` pair: `status` is the only mutable field here, so
`last_updated_by` / `updated_at` already mean "who moved this lead, and when".
Resolved through `user_display_name()` at read time, so a staff rename
propagates. `created_by` is permanently empty — leads arrive unauthenticated.
**If `assigned_to` (§3) lands, revisit that**: a second mutable field makes
`last_updated_by` mean "who last touched it" instead.

**Still deferred:** convert-to-client (§1), `assigned_to` (§3), `source` (§5).

---

## The two emails

Both go through `notifications.services.queue_notification()`; nothing here
touches Brevo. Both work with `Notification.recipient_user` and
`.engagement` unset — the send path never dereferences either — which is what
lets this app email a stranger with no account, and email staff, on the existing
pipeline with no transport change.

### `inquiry_received` → the lead

Sent to the address the lead typed, immediately on a successful submission.
**Exactly one param, `first_name`.** The body is static copy ("…will reach out
within 2 business days…") and the CTA URL is hardcoded in the Brevo template, so
this deliberately echoes back no event type, no dates, no location, no budget,
no details — and not the email address either. The "a confirmation email has
been sent to …" line belongs on the frontend success page, not inside the email.

The one-key context is a useful invariant: any future change that leaks a lead's
own data into this email breaks a single, obvious assertion.

### `inquiry_submitted_internal` → each flagged staff member

**One email per recipient** — N staff means N `queue_notification()` calls, N
`Notification` rows, N Brevo sends. That is required, not merely idiomatic:
`_send_via_brevo` takes a single `to_email`, so status, retry and audit are all
per-address. Collapsing to one multi-recipient send would re-send to everybody
on a partial failure.

Fourteen params: `recipient_name`, then the lead's `first_name`, `last_name`,
`email`, `phone_number`, `contact_mode`, `event_type`, `desired_location`,
`preferred_start_date`, `preferred_end_date`, `budget`, `details`,
`submitted_at` and `inquiry_id`. Dates arrive as ISO strings and `budget` as a
**string** (`_serialise_context` coerces `Decimal` → `str`), so the template adds
the ₦ and thousands separators itself, exactly as `milestone_paid` does. When
the lead chose "not sure yet", `budget` is the literal `"Not specified"` so the
template needs no conditional. `inquiry_id` is the row's UUID, so staff can find
it in the admin.

### Who receives the internal alert

```python
User.objects.filter(receives_inquiry_alerts=True, is_active=True, is_staff=True)
```

`receives_inquiry_alerts` is a boolean on the user, **default `False`**, ticked
per staff member in the user admin — nobody starts receiving leads until you say
so. There is no way to notify an external or shared inbox (`inquiries@…`); the
recipient must be an account.

The `is_staff` term is defensive: `User.save()` keeps `is_staff` in sync with
`role`, so a client account whose flag got ticked by accident can never be
notified, and an offboarded staff member (`is_active=False`) silently stops
receiving leads without anyone editing the flag.

**When nobody is flagged** the inquiry still saves and the lead still gets their
acknowledgement — the submission has not failed. The alert is skipped and a
high-severity structured log event `event="inquiry_no_recipients"` is emitted
(with the `inquiry_id`), so the lead is captured but the fact that nobody was
told is visible. Per `docs/OBSERVABILITY_STANDARD.md` the app emits the signal
and Grafana decides whether to shout; there is no hardcoded alerting and no
silent drop.

---

## Admin

`InquiryForm` is registered in the Django admin as the team's working triage
surface: list display of the key lead fields plus `status` and `created_at`,
filters by `status` / `event_type` / `contact_mode`, search across name, email,
phone and location, `ordering = ["-created_at"]`, and `created_at` read-only.
The fieldsets separate contact, event, details and **Triage** — the last is
where `status` is set by hand.

---

## Tips & gotchas

- **`InquiryForm` is standalone** — no FK to `User`, `Event`, or a portal. It's
  pre-relationship lead capture: the submitter is an anonymous prospect, which is
  why the model carries its own `first_name`/`last_name`/`email`/`phone_number`
  rather than pointing at an account. Nothing in the schema FKs *to* it either.
- **Attribution deliberately does not apply.** There's no acting user to stamp,
  so `InquiryForm` is the one write model outside `core.AttributedModel`. It does
  inherit `UUIDTimestampedModel`, which is where the UUID PK and the timestamps
  come from.
- **`event_type` has one source of truth, and it is not here.** The model sets
  `choices=Event.EVENT_TYPE` directly rather than re-declaring the list, so
  adding an event type is a **single** edit on `events.Event.EVENT_TYPE` and this
  app follows automatically. (This README used to warn you to keep two copies in
  sync — that warning is gone because the duplication is.) You do still need to
  give a new type a reference-code letter in
  `document_hub.services.EVENT_TYPE_CODES`, or its engagements fall back to a
  blank event-type letter in their segment.
- **The 201 is a fixed string on purpose.** If you ever add an `id` or an echo of
  the row to that response, you have built a confirmation oracle on an
  unauthenticated endpoint.
- **The captcha fails open.** A Google outage accepts submissions rather than
  rejecting them. That is the deliberate trade; the rate limit is what actually
  bounds abuse.
- **Tests must patch `_send_via_brevo`.** It begins with `if settings.TESTING:
  return`, so a test that doesn't patch it passes while sending nothing — any
  "email verified" claim without
  `@patch("apps.notifications.services._send_via_brevo")` and an assertion on the
  call kwargs is worthless.
