# API Contract — Client Portal

**Audience: the frontend developer.** Everything you need to build the client's
planning dashboard and the staff side that manages it. Written from the client
side: what to send, what comes back, and what to do about each failure.

Cross-cutting conventions for the whole API live in
[`API_CONTRACT.md`](./API_CONTRACT.md). The backend design rationale lives in
`apps/portal/README.md`. Lead capture has its own contract:
[`API_CONTRACT_INQUIRIES.md`](./API_CONTRACT_INQUIRIES.md).

**Base URL:** every path below is relative to `/api/v1/`.
**Trailing slashes are mandatory.**

---

## 1. The mental model — read this before the endpoints

Three objects, and confusing them is the main source of bugs on this surface.

```
User (the client)
  └─1:1─ ClientPortal        the permanent identity. Created once, by a signal,
         │                   when a client account is registered. Holds almost
         │                   nothing: a welcome message and a team list.
         │
         └─1:N─ EventEngagement    the planning context. Holds ALL the state you
                  │                actually render: current phase, both locks,
                  │                phase details. Exactly ONE is active per
                  │                portal, enforced by a DB constraint.
                  │
                  └─1:1─ Event      the event being planned
```

**The portal is a container; the engagement is the state.** `GET /portal/` reads
through to the active engagement and flattens it for you — every phase and lock
field in that response is sourced from the engagement, not from the portal row.
That flattening is a convenience, and it has one consequence you must handle:

> ### ⚠️ A portal with no active engagement is a normal, expected state
>
> A client account exists from the moment staff registers it, but no event is
> attached until staff activates one. In that window `GET /portal/` returns a
> valid 200 with **`title`, `active_event`, `engagement_id`, `current_phase`,
> `current_phase_display`, `phase_index`, and `phase_updated_at` all `null`**,
> `phase_details` as `{}`, and both lock booleans as `false`.
>
> Render an empty state. Do not treat null `current_phase` as an error, and do
> not index into your phase array with a null `phase_index`.

**A client always has exactly one portal and can only ever see that one.** Staff
can see any portal, and address it by passing `portal_id` — as a **query param on
reads**, in the **request body on writes**. That split is consistent across the
whole API.

---

## 2. Authentication — what the portal frontend must implement

### 2.1 · Tokens

```
POST /api/v1/auth/token/          { "email": "...", "password": "..." }
POST /api/v1/auth/token/refresh/  { "refresh": "..." }
POST /api/v1/auth/token/logout/   { "refresh": "..." }   (authenticated)
```

Send `Authorization: Bearer <access>` on every other request.

| Token | Lifetime | Notes |
|---|---|---|
| `access` | **1 hour** | Cannot be revoked. Its lifetime *is* the window a leaked one keeps working |
| `refresh` | 7 days | Rotates on every use, and the old one is blacklisted — a stolen refresh token works exactly once |

> ### ⚠️ You must refresh on 401 and retry, or users are signed out hourly
>
> The access token is deliberately short. **A 401 does not mean "logged out."** It
> means "refresh and retry the original request." Sign the user out only when the
> *refresh* call itself fails.
>
> There is no `token_expired` code, on purpose — SimpleJWT cannot distinguish an
> expired token from a malformed one, so both arrive as `code: "token_invalid"`
> and publishing a code you could never receive would just make you write a branch
> that never runs. Branch on the 401 itself, not on the code.

Login returns the tokens plus a small user object:

```json
{
  "access": "…", "refresh": "…",
  "user": {
    "id": "…", "email": "…", "first_name": "…", "last_name": "…",
    "force_password_change": false
  }
}
```

### 2.2 · Three login failures, three different remedies

Branch on `code`, never on `detail`.

| Status | `code` | What it means | What the UI does |
|---|---|---|---|
| 401 | `invalid_credentials` | wrong email or password | "try again" |
| 401 | `password_reset_required` | the account has run out of sign-in attempts. **Retrying cannot help, even with the correct password** | send the user into the password-reset flow — do not offer "try again" |
| 429 | `rate_limited` | too many attempts from this address or against this account | show the wait from `Retry-After` |

**The lock behind `password_reset_required`** is 5 consecutive failed attempts,
ageing out after 24 hours. It is checked *before* the password, so a correct
password cannot rescue it — that is required for the ceiling to bound anything.
The only recovery paths are the password reset (which clears it on completion) and
an operator running `manage.py release_login_lock`. A correct sign-in clears the
counter, so ordinary fumbling never accumulates.

**`Retry-After` on a 429 is the real wait**, not a fixed 60 — it can legitimately
be hours if a daily tier filled. Read it; don't retry on a fixed interval.

### 2.3 · `force_password_change` — handle this before your first portal call

Staff-created accounts arrive with a temporary password and
`force_password_change: true`.

> ### ⚠️ Every endpoint except the auth ones returns 403 until it is cleared
>
> ```json
> {
>   "detail": "Password change required. Please change your temporary password before accessing the platform.",
>   "code": "permission_denied",
>   "force_password_change": true,
>   "password_change_url": "/api/v1/auth/force-password-change/"
> }
> ```
>
> This is enforced by middleware, so it fires on `GET /portal/` and everything
> else. **Check the `force_password_change` flag in the login response and route
> to the change-password screen immediately** — don't discover this by taking a
> 403 on your dashboard's first fetch.
>
> The 403 body carries `force_password_change: true` as a distinguishing key, so
> if you do hit it mid-session you can tell it apart from an ordinary permission
> denial without parsing `detail`.

Only `/api/v1/auth/token/…` (login, refresh, logout),
`/api/v1/auth/force-password-change/` and `/admin/` are reachable in that state.

### 2.4 · Roles

`client` sees only their own portal. `staff` and `admin` may act on any portal.
`admin` implies superuser. There is **no public signup** — every account is
created by staff via `POST /users/register/`.

---

## 3. `GET /portal/` — the overview

```
GET /api/v1/portal/                      client: their own portal
GET /api/v1/portal/?portal_id=<uuid>     staff: any portal
```

A client passing someone else's `portal_id` gets `403 permission_denied` — the
staff check runs before the lookup, so the UUID is never a probe.

This one response backs the entire dashboard header. It is also returned by
`PATCH /portal/update/`, `PATCH /portal/phase/` and `PATCH /portal/activate-event/`,
so **you never need a follow-up GET after a write.**

```json
{
  "id": "8c2e…",
  "title": "Ada & Tobi — White Wedding",
  "welcome_message": "Welcome to your planning space…",

  "engagement_id": "4b71…",
  "active_event": "ada-and-tobi-white-wedding",

  "current_phase": "curate",
  "current_phase_display": "No. 03: Curate",
  "phase_index": 3,
  "total_phases": 6,
  "phase_details": { },
  "phase_updated_by_display": "Winnie Adeyemi",
  "phase_updated_at": "2026-08-28T14:02:11Z",

  "contacts_locked": false,
  "event_details_locked": true,

  "team": [
    { "id": "…", "name": "Winnie Adeyemi", "role": "Client Experience Lead",
      "bio": "…", "photo": "https://…" }
  ],
  "contact": { "email": "hello@hephzibahluxe.com", "whatsapp": "+2348023203870" },

  "created_at": "2026-06-01T10:00:00Z",
  "updated_at": "2026-08-28T14:02:11Z",
  "created_by_display": "",
  "last_updated_by_display": "Winnie Adeyemi"
}
```

### Field notes

| Field | Notes |
|---|---|
| `active_event` | **the event's `slug`, a string** — not a UUID and not a PK. This is what you pass to every event-scoped route (`/event/<slug>/…`) |
| `engagement_id` | the engagement UUID. Some other apps' routes are engagement-scoped rather than event-scoped — use this, not `id` |
| `id` | the **portal** UUID. This is what goes in `portal_id` on staff writes |
| `phase_index` / `total_phases` | 1-based position and the total, so the "3 of 6 phases" gauge needs no client-side phase list. `phase_index` is `null` with no active engagement |
| `phase_details` | a free-form JSON dict for per-phase checklist/sub-status state. `{}` when unset |
| `phase_updated_by_display` | **specifically who moved the phase** — use this for the Planning Stage "Last updated by …". It stays put when something unrelated changes |
| `last_updated_by_display` | the *generic* attribution: moves on **any** save, including a lock toggle or a welcome-message edit. Not the same thing as the line above. Don't use it for the phase |
| `created_by_display` | usually `""` — portals are created by a signal at registration, so there is no acting user. Expected, not a bug |
| `team` | the minimal shape: `id`, `name`, `role`, `bio`, `photo` only. No email or phone — see §6 |
| `contact` | admin-configured business contact. Build `mailto:` and `wa.me` links from these two strings directly. **Either may be an empty string** — hide the affordance rather than rendering a dead link |

**The contact panel creates nothing.** Opening it is a pure client-side link-out;
no conversation record is created. Staff log the resulting exchange separately.

### The six phases

| Value | Label |
|---|---|
| `connect` | No. 01: Connect |
| `align` | No. 02: Align |
| `curate` | No. 03: Curate |
| `envision` | No. 04: Envision |
| `orchestrate` | No. 05: Orchestrate |
| `deliver` | No. 06: Deliver |

Render `current_phase_display` rather than mapping the value yourself. Use the
value only for logic (progress bar position, filtering).

---

## 4. The two locks

Two independent booleans on the engagement, both surfaced on the overview. They
gate **client** writes only — staff can always write regardless of either.

| Flag | Gates | Error on a blocked client write |
|---|---|---|
| `contacts_locked` | adding/editing event contacts | 403 `code: "contacts_locked"` |
| `event_details_locked` | editing the Event and its EventDays | 403 `code: "event_details_locked"` |

They are separate on purpose: staff may want to freeze the date and venue while
leaving the contact list open, or the reverse.

**Read both from the overview and disable the affordance client-side.** The 403 is
the backstop, not the UX — a client who fills in a form and then discovers it was
read-only has already lost the work.

**Deletion is never gated by either flag.** Clients can never delete an event or
an event day regardless of lock state.

**Locks can appear without any staff action.** If auto-lock is enabled (an
admin-side setting with no API), reaching a configured phase sets one or both
automatically. It is **lock-on-reach only — it never auto-unlocks.** So a lock can
flip to `true` as a side effect of a phase change; re-read the overview after one
rather than caching the lock state independently. The phase-change response
already carries the new values.

---

## 5. Staff writes

All four require staff/admin. All take **`portal_id` in the body** (not the query
string), and all except the team-assignment pair return the full overview object.

### 5.1 · `PATCH /portal/update/`

```json
{ "portal_id": "8c2e…", "welcome_message": "Welcome to your planning space…" }
```

**`welcome_message` is the only writable field.** Phase and active-event changes
have dedicated endpoints below; anything else in the body is ignored.

Returns the full overview.

### 5.2 · `PATCH /portal/phase/`

Two modes:

```json
{ "portal_id": "8c2e…", "phase": "envision" }     // set explicitly
{ "portal_id": "8c2e…", "advance": true }          // step to the next phase
```

Returns the full overview — including any lock that auto-lock just applied, and
the refreshed `phase_updated_by_display` / `phase_updated_at`.

**Sending the client an email is a side effect of this call.** A `phase_advanced`
notification goes out on every phase change, including a sideways or backward one.
There is no "quiet" mode. Worth a confirmation step in the staff UI.

Failures — both are `400` with `code: "invalid_transition"`:

| `detail` | Cause |
|---|---|
| `"No active engagement found for this portal."` | the portal has no active engagement. **This is the common one** — disable the phase control entirely when `engagement_id` is null |
| `"Portal is already at the final phase (Deliver)."` | `advance: true` at `deliver` |

An unrecognised `phase` value is a `400 validation_error` with the allowed choices
in `errors`, which is a different code — branch accordingly.

Note the phase is *not* a validated state machine: any phase may be set from any
other. Only `advance` is ordered.

### 5.3 · `PATCH /portal/activate-event/`

```json
{ "portal_id": "8c2e…", "event_slug": "ada-and-tobi-white-wedding" }
```

Deactivates the current engagement and creates or reactivates one for the given
event, atomically. An unknown slug is `400 validation_error`.

> ### ⚠️ This looks like data loss to the client, and it isn't
>
> Switching is **non-destructive** — nothing is deleted. But meetings,
> conversations, reminders and documents all read through the *active*
> engagement, so the previous event's content simply stops appearing. From the
> client's side that is indistinguishable from deletion.
>
> When the call moves away from a **different** already-active event, the response
> carries two extra keys on top of the normal overview:
>
> ```json
> {
>   "…": "…normal overview fields…",
>   "previous_engagement_content": {
>     "meetings": 4, "conversations": 12, "reminders": 3, "documents": 7,
>     "client_documents": 2, "invoices": 1, "receipts": 1, "payment_milestones": 3
>   },
>   "note": "Switching the active event doesn't delete anything — …"
> }
> ```
>
> **Surface this.** Show the counts and the fact that switching back restores the
> view. Both keys are **absent** when there was no previous engagement or the
> target was already active, so treat their presence as the trigger.

Use this rather than deleting and recreating events: deleting an event
cascade-deletes its engagement and everything attached to it, for real.

### 5.4 · Team assignment

```
GET    /api/v1/portal/team/                     client: own team · staff: ?portal_id=
POST   /api/v1/portal/team/assign/              { portal_id, team_member_id }
DELETE /api/v1/portal/team/remove/              { portal_id, team_member_id }
```

`GET /portal/team/` returns a bare array of the minimal shape — the same list
embedded as `team` in the overview. Use the overview's copy for the dashboard; use
this route only if you need it standalone.

**These two writes do not return the overview.** They return a flat message, so
re-fetch if your UI shows the team:

| | Response |
|---|---|
| assign | `201` · `{"detail": "Team member assigned."}` |
| remove | `200` · `{"detail": "Team member removed."}` |

**Assigning is idempotent** — assigning someone already on the portal is a silent
no-op with the same 201, so a double-click is safe. **Removing someone who isn't
assigned is a `400 validation_error`** (`"This team member is not assigned to the
portal."`), which is an asymmetry worth handling.

Both send `portal_id` in the **body**, including the DELETE.

---

## 6. Team member profiles (global, staff-managed)

Distinct from §5.4: a `TeamMember` is a **global profile** created once and
assignable to many portals. Deleting one cascades and removes every assignment.

```
GET    /api/v1/portal/team-members/                 list all · ?is_default=true
POST   /api/v1/portal/team-members/create/          create
PATCH  /api/v1/portal/team-members/<uuid>/          update
DELETE /api/v1/portal/team-members/<uuid>/          delete
```

All staff-only. **Not paginated** — a bare array.

Two shapes, and the difference is deliberate:

| Shape | Fields | Where |
|---|---|---|
| **full** | `id`, `name`, `role`, `bio`, `photo`, `email`, `phone`, `is_default`, `created_by_display`, `last_updated_by_display` | this section's routes (staff) |
| **minimal** | `id`, `name`, `role`, `bio`, `photo` | the overview's `team` array and `GET /portal/team/` (client-visible) |

**`email` and `phone` are staff-only and are not in the client-visible shape.**
Don't build a client-side "email your planner" feature from the team array — the
`contact` block on the overview is the client-facing route.

**`is_default`** flags a member as auto-assigned to **every newly created portal**
("Meet Your Team"). Seeding fires on portal creation only, so toggling it does not
retro-assign to existing portals and does not re-add someone staff previously
removed. `?is_default=true` lists just the defaults.

**`photo` upload:** max **5 MB**, `image/jpeg`, `image/png` or `image/webp`. A
rejection is an ordinary field-level `400 validation_error` with `photo` named in
`errors`. **Check size and type in the browser first** — the web tier runs a
120-second request timeout, and on a congested mobile uplink a large file risks
being killed mid-transfer instead of refused cleanly.

`DELETE` returns `{"detail": "Team member deleted."}`; `PATCH` returns the full
shape.

---

## 7. Errors — the whole surface

Every error response, project-wide, carries the same envelope:

```json
{ "detail": "human readable", "code": "machine_code", "errors": { "field": ["…"] } }
```

`errors` is present only for field-level validation. **Branch on `code`, never on
`detail`** — detail strings are not a stable interface.

| Status | `code` | On this surface it means |
|---|---|---|
| 400 | `validation_error` | bad input. Read `errors` for the fields |
| 400 | `invalid_transition` | a legal-looking state change that isn't allowed here (no active engagement, already at `deliver`) |
| 401 | `invalid_credentials` | wrong email or password |
| 401 | `password_reset_required` | account locked — send to password reset, don't offer retry |
| 401 | `token_invalid` | expired or malformed token → **refresh and retry** |
| 401 | `authentication_required` | no token sent at all |
| 403 | `permission_denied` | wrong role, or another client's portal. Also the `force_password_change` block — check for that key |
| 403 | `contacts_locked` | client write blocked by the contacts lock |
| 403 | `event_details_locked` | client write blocked by the event-details lock |
| 404 | `not_found` | no such portal / team member. Also used where *existence* is sensitive |
| 429 | `rate_limited` | a per-endpoint limit you tripped. `Retry-After` is accurate |
| 429 | `throttled_global` | the shared anonymous ceiling — may have been spent by someone else behind the same NAT, so retrying sooner won't help |
| 500 | `internal_error` | a bug. Report it with the `X-Request-ID` header |

**Every response carries `X-Request-ID`.** Log it client-side and quote it in bug
reports — it correlates the whole request across the backend logs, including any
background work it triggered. You may also *send* one and it will be honoured.

### The one limit that applies to logged-in traffic

`120 requests per minute per account`, and no daily cap. No human reaches it; a
polling or retry loop reaches it in about a second and recovers within the minute.
If you are seeing `rate_limited` on authenticated portal routes, you have a loop —
fix the loop rather than asking for the ceiling to be raised.

---

## 8. Integration checklist

- [ ] Refresh on 401 and retry; sign out **only** when the refresh itself fails
- [ ] Check `force_password_change` in the login response before the first portal
      fetch; route to the change screen
- [ ] Branch on `code`, never on `detail`
- [ ] Handle `password_reset_required` as its own path — not "wrong password"
- [ ] Empty state for a portal with no active engagement (null phase, null
      `engagement_id`)
- [ ] Use `active_event` (a **slug**) for event routes, `engagement_id` for
      engagement routes, `id` for `portal_id` on staff writes
- [ ] Render `*_display` fields; don't map phase values to labels client-side
- [ ] `phase_updated_by_display` for the Planning Stage line — not
      `last_updated_by_display`
- [ ] Disable locked affordances from `contacts_locked` / `event_details_locked`
      rather than relying on the 403
- [ ] Re-read locks after a phase change (auto-lock may have fired)
- [ ] `portal_id` in the **body** on writes, in the **query string** on reads
- [ ] Confirm before `activate-event`; surface `previous_engagement_content` and
      `note` when present
- [ ] Confirm before a phase change — it emails the client every time
- [ ] Re-fetch the team after assign/remove (those return a message, not the
      overview)
- [ ] Validate photo size (5 MB) and type client-side before upload
- [ ] Hide the contact affordances when `contact.email` / `contact.whatsapp` are
      empty strings
- [ ] Capture `X-Request-ID` for bug reports
