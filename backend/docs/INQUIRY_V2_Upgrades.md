# Inquiry v2 — upgrades and open operations

**What this file is.** The work queue for the next implementation pass. Two kinds
of thing live here:

* **Part A** — inquiry features deferred out of the v1 build.
* **Part B** — cross-cutting items that are *not* inquiry features but are
  deliberately-open properties of the platform. They were moved here out of
  [`RATE_LIMITING_GUIDE.md`](./RATE_LIMITING_GUIDE.md) so that file could stay a
  guide instead of doubling as a backlog.

Each item records *why* it's worth doing and *what it reuses*, so picking it up
later doesn't mean re-deriving the reasoning.

**Part C** keeps the reasoning behind items that are now closed. Nothing there is
work; it is there so a future reader doesn't re-propose a fix that was already
costed and rejected, or re-open a defect that was already fixed.

> **Naming note.** This file was `INQUIRY_V2_BACKLOG.md`. Several code comments
> and the app README still cite the old name — see Part D. `docs/RATE_LIMITING_AUDIT.md`
> and `docs/INQUIRY_IMPLEMENTATION_PLAN.md` no longer exist; the audit's
> conclusions were folded into `RATE_LIMITING_GUIDE.md`.

---

# Part A — Inquiry features

## A1. Convert an inquiry into a client — the funnel gap

**The problem.** The platform's chain is `User → ClientPortal → EventEngagement
→ Event`. An inquiry sits outside it entirely. With a triage status but no
conversion path, the workflow is: staff mark a lead "Converted", then manually
retype the same first name, last name, email, phone, event type, dates and
location into `POST /users/register/`. The same data entered twice, and no link
survives between the lead and the client it became — so "which marketing channel
produced this ₦80m wedding?" is unanswerable.

**What it reuses (this is the point — almost nothing is new):**

| Step | Existing machinery |
|---|---|
| Create the account | The same service `POST /users/register/` calls — cryptographically secure temp password, credentials email through `queue_notification`, `force_password_change` set, middleware enforcing it |
| Create the portal | Nothing to write. The existing signal auto-creates `ClientPortal` on client-user creation (`apps/portal`) |
| Seed the team | Also nothing. The same signal fires `seed_default_team_members`, so the new portal arrives with its "Meet Your Team" contacts already assigned |
| Offboarding safety | Already covered — `PATCH /users/<email>/status/` is reversible, and users are FK targets rather than deletable |
| Reference codes | `document_hub` already derives `HL-PSW006-INV001` from the event; a converted event gets its segment the same way |

**What is new:** two nullable FKs on `InquiryForm` — `converted_user` and
`converted_event`, both `on_delete=SET_NULL` matching the house default — plus
one staff endpoint and one admin action. Setting them flips the status to
`converted`.

**The honest limit, and why conversion must not be fully automatic.** `Event`
has *type-conditional required fields* — `bride_name` and `groom_name` for a
wedding, `honoree_name` for a birthday — which the inquiry form never asks for,
and `Event.title` is derived from them. So conversion can create the **user and
portal** (fully determined by the inquiry) but must **pre-fill** event creation
for staff rather than silently manufacturing a half-populated `Event` whose title
is wrong and whose reference-code segment is then frozen against that wrong
title. Reference segments are assigned once and never change — getting it wrong
is expensive to undo.

**Suggested shape:** `POST /inquiries/<uuid>/convert/` returning the created user
plus a pre-filled event payload for the staff form to submit as a second step.
Idempotent — a second call on an already-converted inquiry returns the existing
user rather than creating a duplicate account.

**Note the transition table already anticipates this.** `CONVERTED` is
near-terminal in `services.VALID_TRANSITIONS` (its only exit is `ARCHIVED`)
precisely because conversion will create a user and an event, and reversing it
would orphan both.

---

## A2. `assigned_to` — lead ownership

A nullable FK to the staff user, so "whose lead is this?" is answerable and
filterable. Reuses the same `accounts` table already reused for the
`receives_inquiry_alerts` recipient flag, and the same `user_display_name()`
helper. Pairs naturally with A1 and A3 — an owner, a status, and an audit trail
are one feature in three parts, which is an argument for doing all three together
rather than drip-feeding migrations onto the same table.

> **⚠️ Landing this changes what attribution means.** `InquiryForm` currently uses
> the generic `AttributedModel` pair rather than a bespoke `status_updated_by`,
> and the entire justification is that **`status` is the only mutable field on the
> model** — so `last_updated_by` / `updated_at` already *is* the status
> attribution. A second mutable field breaks that: `last_updated_by` degrades to
> "who last touched it", and a status-specific pair becomes the right answer after
> all. Budget for that migration as part of this item, not after it.

---

## A3. `source` / channel attribution

A field the frontend populates from a query parameter or referrer — Instagram,
referral, Google, direct — so lead volume by channel is measurable. **No in-house
precedent**: this would be the first field of its kind, which is why it was
deferred. Cheap to add now, impossible to backfill later, and it only becomes
genuinely valuable once A1 exists (channel → booked revenue).

If it lands, it is a **second mutable-ish field** only if staff can edit it. Keep
it write-once from the public payload and the A2 warning above does not apply.

---

## A4. Real-world gaps not yet addressed

- **Consent record.** The form shows a reCAPTCHA privacy notice but the backend
  stores no consent artefact — no timestamp, no policy version, no marketing
  opt-in. For a brand handling international clients this is a mild compliance
  gap. Cheap: two fields captured at submit.
- **Phone format.** `phone_number` is a `CharField(20)` with no validation, and
  the form's country-flag selector means the value's shape depends entirely on
  frontend behaviour. The contract documents E.164 (`+234…`); nothing enforces it.
  A validator would be the first in this app — deliberate, since rejecting a lead
  over phone formatting is worse than storing a messy string staff can still read.
  **Note the interaction with dedupe:** `+2348012345678` and `080 1234 5678` are
  different fingerprints, so an unnormalised phone field means a resubmit with a
  reformatted number is a second lead rather than a collapsed duplicate.
- **Triage SLA.** The client email promises a reply "within 2 business days" and
  nothing measures whether that happens. Now that A-shipped status attribution
  exists (Part C1), `updated_at` minus `created_at` makes it reportable — and a
  task in the `daily_maintenance` cron group could flag breaches, reusing
  `ScheduledTaskSettings` and the existing digest pattern.

---

## A5. reCAPTCHA is in monitor mode and must be taken out of it

**This is a live operational TODO, not a feature.** `.env.example` ships
`RECAPTCHA_MIN_SCORE_SUBMIT_INQUIRY=0.0`, which is deliberate — every token is
still verified, so the reCAPTCHA console accumulates a real score distribution,
and the `success` and `action` checks still reject. But **nothing is turned away
on score**, so the score half of the v3 integration is currently doing nothing.

Two things to do, in order:

1. **Confirm `RECAPTCHA_SECRET_KEY` is actually set in production.** Unset, the
   whole module is a no-op that returns `True` and the two rate tiers are the only
   bot defence on public lead capture. Nothing at boot will tell you — it is
   deliberately exempt from the fail-fast check that covers `BREVO_API_KEY` and
   `CACHE_REDIS_URL`, because the app must start without it.
2. **Watch the console's score distribution, then raise the threshold.** Err low:
   a wrongly rejected inquiry is a lead the business never learns it had.
   `RECAPTCHA_MIN_SCORE_DEFAULT` stays at `0.5` and should not be moved — it
   governs a future endpoint someone wires up and forgets.

Watch `recaptcha_low_score` while the threshold is 0.0: it will not fire, which is
the point. Once raised, it becomes the signal that the number is too high.

Also confirm the key is a **v3** key. A v2 secret returns no `score`, which the
code accepts (for a v2 key `success` genuinely is the verdict) but logs at ERROR
as `recaptcha_v2_key_in_use` — because every threshold is then silently inert.

---

# Part B — Cross-cutting, deliberately open

Moved out of `RATE_LIMITING_GUIDE.md`. Neither is an oversight; both are known
properties with a stated cost, recorded here so the decision to keep them is a
decision rather than an accident.

## B1. django-ratelimit windows are fixed, not sliding — 2x at the boundary

A 10/m limit permits 10 requests at 11:59:59 and 10 more at 12:00:00 — twenty in
two seconds. Read every per-endpoint rate as "roughly 2x this, briefly."

Two things bound how much this matters:

- **The DRF ceilings are not affected.** `SimpleRateThrottle.allow_request` keeps
  a *list of timestamps* and drops those outside the duration, so `anon` and
  `user_burst` are already sliding-window. This is a django-ratelimit property
  only.
- **The controls that need a hard bound don't use windows at all.**
  `User.failed_login_count` ages from `failed_login_at`, the email-keyed cache
  counter sets its TTL once on the first failure rather than pushing it forward,
  and `PasswordResetToken.attempt_count` lives on the row. Five failures is five
  failures however they are spaced. The security-critical limits are already
  outside this problem; the windowed tiers are the smoothing layer around them.

**Why it stays open.** The fix is a sliding-window counter (keep the previous
window's count too, weight it by the overlap). The cost is not the ~40 lines —
it's that `get_usage` is called *inside* django-ratelimit's decorator, so
adopting a different algorithm means replacing the decorator on every limited
endpoint. That is the whole rate-limiting surface rewritten to remove a 2x on
limits whose real bounds sit elsewhere.

**The pattern to use instead:** where a hard bound genuinely matters, count on a
row rather than in a window — which is what the counters above already do.

## B2. The authenticated surface has a burst limit and nothing else

`user_burst` at 120/m is the whole story for logged-in callers — no hourly cap,
no daily cap. This is a deliberate trade, documented in `apps/core/throttling.py`,
and it's correct for the stated threat model (runaway clients, not bad humans):
every account here is staff-created, so a misbehaving *human* is switched off with
`set_user_status` rather than rate-limited.

Two consequences to be aware of rather than to fix:

- A compromised staff token can make 120 requests a minute for as long as it's
  valid. The control is revocation, not throttling. Note the access token lasts
  **1 hour** and cannot be revoked at all — only refresh tokens are blacklisted on
  rotation — so an hour is the real floor on that exposure.
- `POST /users/register/` sends an email and is staff-only, so its only bound is
  120/m — i.e. a compromised staff account could send 120 emails a minute.

The mitigation that already shipped for the adjacent risk is **pagination**, not a
limit: `GET /users/` and `GET /inquiries/` used to return the whole table in one
response, and a rate limit cannot help with that because one request is enough.

---

# Part C — Closed, kept for the reasoning

## C1. Status attribution and validated transitions — ✅ SHIPPED 2026-08-29

**Built differently from the original description, deliberately.** The item was
written against the `EventEngagement.phase_updated_by` precedent. Since then
`apps/core/models.AttributedModel` (`created_by` / `last_updated_by`, with
`save_with_attribution` / `stamp_attribution` and `AttributionSerializerMixin`)
became the project's generalised convention, and `phase_updated_by` is its
pre-generalisation ancestor.

`InquiryForm` inherits `AttributedModel` instead of growing a bespoke
`status_updated_by` pair, because **`status` is the only mutable field on the
model** — so `last_updated_by` / `updated_at` already *is* the status attribution
the item asked for, without a third name for one idea. A portal has many mutable
fields and must single one out; an inquiry has exactly one.

Migration `0004_inquiryform_created_by_inquiryform_last_updated_by`. `created_by`
is permanently NULL (leads arrive unauthenticated) and that is correct.
Transitions are guarded by `services.VALID_TRANSITIONS` in the `apps/meetings`
shape, raising `INVALID_TRANSITION`; same-status is an accepted no-op, diverging
from meetings so a frontend double-click is not a 400. The admin got a
`save_model` hook, since admin saves bypass the serializer helpers.

**⚠️ If A2 (`assigned_to`) lands, revisit this** — see the warning there.

## C2. `GET /inquiries/summary/` — pipeline counts — ✅ SHIPPED 2026-08-29

Per-status tallies in one request, so a staff dashboard doesn't fetch every lead
to count them client-side. Response shape mirrors
`GET /event/<slug>/contacts/summary/` exactly — a flat list of
`{status, status_display, count}`.

Two deliberate departures from the older summary endpoints: it resolves in **one**
aggregate query rather than one `COUNT` per choice (the JSON is identical, and
avoiding work is the whole reason this endpoint exists), and it shares
`_filtered_inquiries()` with `list_inquiries` so the tallies cannot drift from the
list. `?status=` is ignored — filtering a per-status tally by status answers
nothing. Empty statuses are always present so dashboard columns stay stable.

## C3. Double-submit: the dedupe window and the rate limit disagreed (**R5**) — ✅ RESOLVED

**Resolved in two moves, and the second one is the fix this section had costed
and shelved.**

**Move 1 (2026-08-20) — the key shape.** The original limit was `3/h` on
`(IP, email)`, and the audit found the *key shape* was the real problem, not the
number: because the email on a public form is attacker-chosen and free, that limit
capped nothing — vary the address and one machine could submit without limit —
while spending all of its strictness on the one honest lead who double-clicks. It
was replaced with two tiers (a short-window burst on `(IP, email)` **plus** an
hourly tier on the IP alone), so the fumbled window clears in ten minutes instead
of at the next hour boundary.

**Move 2 — `apps/inquiries/dedupe.py`.** This section's table listed four candidate
fixes and marked *"Move dedupe to the key callable, so a repeat submit doesn't
count"* as **the structurally correct fix — one decision point instead of two**.
That is what shipped. django-ratelimit accepts a **callable** `rate`, and returning
`None` from it makes `get_usage` skip the check entirely — no increment, no block.
So the burst tier's rate is now `dedupe.burst_rate`, which asks "have I already
accepted this exact submission?" and declines to count the request if so. That is
the library's own supported mechanism; the rejected alternative was reaching into
its private counter to decrement, which is not a thing to build a lead-capture
path on.

**What remains, and why it is inconsequential.** The marker is written at the *end*
of the view, after validation, the reCAPTCHA round-trip and the insert — so a
genuine double-click firing ~100–300ms apart can still reach the rate callable
before the marker exists and be counted. The burst count of **6** is sized to work
under both readings: three submissions if every double-click costs two, six if
none do. The pessimistic case used to be two submissions at a count of 4 — submit,
spot a typo, resubmit, done — which was the actual complaint.

**Two side-effects of the rework worth remembering:**

- The dedupe key now fingerprints the **whole validated payload**, not the email.
  The email-only version was an *email lockout*: a lead resubmitting inside the
  window with a corrected date got a 201 and no row, and the correction was
  destroyed silently. That was a different and worse defect than this one.
- Every swallowed submit emits `inquiry_dedupe_hit`, which is the measurement this
  section always asked for before retuning. Use it rather than a guess.

## C4. The global anon throttle was one shared bucket — ✅ RESOLVED 2026-08-20

The original: DRF's `'anon': '50/day'`, keyed on the client IP, global — so it
applied to the inquiry endpoint *on top of* its own limiter, and because it was
one bucket per IP across **all** unauthenticated traffic, failed logins,
password-reset requests and inquiry submits all drew down the same 50 a day.
Behind NAT/CGNAT that meant a venue whose staff spent the morning failing to log
in could exhaust the day's allowance and leave a genuine lead from the same
building unable to submit at all.

The audit also found three things the original write-up did not: the `50/day` was
bypassable with a single `X-Forwarded-For` header, the proxy's own address was
inside the bucket key (so every counter silently reset whenever the platform edge
rotated), and a `user: 500/day` sliding budget existed on the *authenticated*
surface that nothing mentioned.

**Resolution:** the inquiry endpoint opts out of the shared pool entirely
(`@throttle_classes([])`, the only such opt-out in the project); the throttles now
take their client identity from `apps.core.ratelimit`; both rates are env-driven;
`anon` was raised to `1000/day` and demoted to a safety net; each anonymous
endpoint got its own daily cap; and `user: 500/day` was replaced by a `120/m`
per-account burst ceiling.

**One correction to the original's own advice.** It said the ceiling being hit
*"is measurable (429s carry `code: "rate_limited"`)"*. It was not — both limiters
emitted that same code and neither logged anything, so a 429 could not be
attributed to either one. That is now fixed: `event="rate_limited"` plus a distinct
`throttled_global` code, which is what finally makes the measurement possible.

## C5. `auth_login` counted successful logins — ✅ CLOSED 2026-08-29

The limit counted *every* POST, successes included, and one office IP is not one
person: twelve staff logging in within the same minute meant the later arrivals
got a 429 for doing nothing wrong.

Fixed at the root rather than by raising the number. `auth_login` now counts
**failed** attempts only — `apps/accounts/login_guard.py` checks the tiers on the
way into the view and increments them on the way out, and only when authentication
actually failed. A correct sign-in costs nothing, so an office of any size can log
in.

Login is consequently the one API endpoint **not** wrapped at the URL, and its
view overrides `handle_exception` to re-raise `Ratelimited` — that exception
subclasses Django's `PermissionDenied`, which DRF maps to 403, so without the
override a limited login would answer with the wrong status. See
[`adr/0002-login-failure-tracking.md`](./adr/0002-login-failure-tracking.md).

## C6. `auth_login_account` had no daily backstop — ✅ CLOSED 2026-08-29

A single email was exposed to 10/h × 24 = **240 password guesses a day** from an
unlimited number of IPs, forever and silently, with no account lockout behind it.

Not fixed by adding `auth_login_account_daily` on its own, which would have been a
denial-of-service vector: anyone knowing a staff email could spend that account's
allowance from many IPs, one attempt each so no per-IP tier fires, and refuse the
real person. That is very likely why the tier shipped without a daily sibling.

What shipped instead, as one change: the login tiers count failures only (C5);
`User.failed_login_count` with a ceiling of 5 matching
`PasswordResetToken.MAX_VERIFY_ATTEMPTS`; a second email-keyed cache counter so the
lock is reported identically for addresses with and without an account; and
`auth_login_account_daily` (50/d) as a backstop above the ceiling, safe to add only
once the tiers counted failures. See ADR-0002 and `RATE_LIMITING_GUIDE.md` §5.

**Residual, accepted:** an attacker can force an account holder to complete a
password reset. That is the standard trade for having any per-account bound.

## C7. `Retry-After: 60` was a guess — ✅ CLOSED 2026-08-29

Every django-ratelimit 429 carried the same flat 60, because the middleware
receives only the exception and django-ratelimit raises a bare `Ratelimited()`
that says nothing about which tier fired or when its window closes. A client
blocked by `auth_login_daily` was told to come back in a minute and then refused
for up to 24 hours: 1,440 polite, pointless retries.

`get_usage` already returned `time_left`; it just wasn't reaching the renderer.
Two routes now carry it (`exception.retry_after`, then `request.rate_limit_tiers`),
the largest wait wins when more than one ceiling is full, and
`RATELIMIT_RETRY_AFTER_SECONDS` survives as the last-resort fallback rather than
the only answer. Full mechanics in `RATE_LIMITING_GUIDE.md` §6.

---

# Part D — Considered and rejected

Do not revisit without new information.

| Idea | Why not |
|---|---|
| Register `inquiry` in the deep-link registry (`apps/core/deeplinks.py`) | Every target type must resolve its own engagement, and an unresolvable engagement returns `None`, which is never read as "allowed". A pre-relationship lead has no engagement, so this would violate the registry's core invariant rather than extend it. After A1, the *converted event* is already linkable — the need disappears |
| Email the client when their status changes | Internal triage isn't client-facing. There is no acceptable version of a "you have been marked Lost" email, and `NotificationTypeSettings` can only disable a type, not un-send one |
| Put a Django-admin URL in the internal alert email | Requires a second base-URL env var beside `FRONTEND_BASE_URL`. `config/settings.py` is explicit that two env vars naming one deployment is how staging links reach production inboxes. Revisit only when a staff *frontend* page exists, at which point the existing `absolute_url` helper covers it |
| Purge old inquiries | Leads are business records. Note the notifications table's weekly purge does delete `SENT` rows older than 90 days, so the internal alert's copy of the lead's details ages out — that's correct, the `InquiryForm` row is the record of truth |
| A second `ScheduledTaskSettings` scheduled job for inquiries | Nothing in v1 is time-relative. Digests exist for "this becomes relevant N days before X"; a lead notification has a single triggering moment and should stay event-driven. Only A4's SLA breach flag would justify one |
| Give the public 201 an `id` or an echo of the stored row | It would be a confirmation oracle on an unauthenticated endpoint. The fixed string is the design, not an oversight |
| Add a general `PATCH /inquiries/<uuid>/` | What the lead typed is immutable. `status` has its own sub-route precisely so a general update can never quietly rewrite the client's own words |

---

# Part E — Documentation debt

Found while re-verifying this file against the code on 2026-08-30. None of it
changes behaviour; all of it misleads a reader.

| Where | Says | Actually |
|---|---|---|
| `apps/inquiries/README.md` § Tips & gotchas | "`InquiryForm` is standalone — no FK to `User`, `Event`, or a portal" and "Attribution deliberately does not apply … the one write model outside `core.AttributedModel`" | It inherits `AttributedModel` and carries `created_by` / `last_updated_by` FKs to `User`. The same README describes that correctly two sections earlier — it contradicts itself |
| `apps/inquiries/models.py`, `services.py`, `README.md`, `tests.py` | cite `docs/INQUIRY_V2_BACKLOG.md` | this file, renamed |
| `apps/core/ratelimit.py`, `throttling.py`, `tests.py`, `config/settings.py` | cite `docs/RATE_LIMITING_AUDIT.md` | folded into `RATE_LIMITING_GUIDE.md` |
| `docs/brevo-templates/inquiry_*.md` | cite `INQUIRY_IMPLEMENTATION_PLAN.md` | deleted |
| 19 files incl. `apps/portal/`, `apps/events/`, `apps/contacts/` | cite `docs/FAILURE_POINTS_AUDIT.md` | deleted |
