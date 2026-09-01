# Hephzibah Luxe — API Contract (v1)

> **Not a route list.** Exact paths and HTTP methods live in each app's
> `urls.py` (every `path()` there carries a `# GET`/`# POST`/etc. comment) —
> that file is the source of truth for routing, and duplicating it here would
> just be a second place to go stale. This document holds the **cross-cutting
> conventions** every endpoint follows, plus a pointer to where each app's
> *behavioral* design decisions are written up. Per-app logic (what a field
> means, why a workflow is shaped the way it is, how one app's data links to
> another's) lives in that app's `README.md`.

> **Building a frontend?** Two apps have their own end-to-end, client-side
> contracts — request/response shapes, enum vocabularies, reCAPTCHA token
> minting, error handling and an integration checklist:
>
> * [`API_CONTRACT_INQUIRIES.md`](./API_CONTRACT_INQUIRIES.md) — the public lead
>   form and the staff lead inbox
> * [`API_CONTRACT_PORTAL.md`](./API_CONTRACT_PORTAL.md) — auth/session handling
>   and the client planning dashboard
>
> Start there; this page is the layer of conventions underneath them.

## Conventions

- **Base:** every route is served under **`/api/v1/`**. The Django admin
  (`/admin/`) is the only thing outside the prefix.
- **Auth:** JWT (SimpleJWT) in `Authorization: Bearer <access>`. `access` lives
  **1 hour**, `refresh` 7 days (rotating, blacklist-on-rotate). The short access
  lifetime is deliberate — an access token cannot be revoked, so its lifetime is
  exactly how long a leaked one keeps working. **This requires the frontend to
  refresh on 401 and retry**; a client that treats a 401 as "logged out" signs
  users out hourly.
- **Roles:** `client` (celebrant) sees only their own portal's resources; `staff`
  / `admin` may act on any portal, usually by passing `?portal_id=<uuid>` (reads)
  or `portal_id` in the body (writes). A fourth role, `developer`, sits above
  `admin`: it passes every `staff`/`admin` check (so no endpoint behaves
  differently for it) and additionally cannot be demoted, renamed,
  re-passworded, deactivated or deleted by anyone else. It is granted by the
  `PLATFORM_DEVELOPER_EMAILS` deployment setting rather than on the platform, so
  `POST /users/register/` refuses both `"role": "developer"` and any
  registration at a configured developer address (400), and
  `PATCH /users/<email>/status/` against one returns **403**. It appears in
  `GET /users/` and is filterable with `?role=developer` like any other. See
  `docs/adr/0004-protected-developer-role.md`.
- **One carve-out from the two rules above:** `POST /api/v1/inquiries/` (public
  lead capture) takes no token and applies no role scoping. The auth endpoints
  (`auth/token/*`, `auth/password-reset/*`) are necessarily unauthenticated
  too, but they act on an account that already exists; the inquiry endpoint is
  the only place an anonymous caller **creates a record**. Everything else on
  this page still applies to it — same `/api/v1/` prefix, same error envelope,
  same codes — and it is rate-limited on two tiers (`(IP, email)` for a
  fumbling human, IP alone for a script varying the email) and optionally
  reCAPTCHA-gated, with a 201 that carries a fixed message, no id and no echo
  of the stored row. It is also the one endpoint that opts out of the
  project-wide anonymous throttle, so lead capture never shares a ceiling with
  failed logins. Reads and every other write stay authenticated. See
  `apps/inquiries/README.md`.
- **Success:** the serialized object, or a list (pagination is being migrated
  per endpoint — see `apps/core/pagination.py`. Two strategies coexist:
  `StandardCursorPagination` for unbounded lists, `StandardPageNumberPagination`
  where the UI itself is numbered-page shaped, e.g. Budget Payment History).
  `GET /inquiries/` is **always** paginated, not opt-in: a lead row carries a
  name, email, phone number and budget, and a rate limit bounds how many
  requests a caller makes rather than how much each one returns — so the page
  is what bounds the exposure. It uses `InquiryPageNumberPagination` — the same
  envelope and query params as every other paginated portal list, at **10 per
  page** instead of the shared default of 7. `?page_size=` widens it to 50.
- **Four lists paginate unconditionally**, and the rule is not "big lists" — it
  is *any list a staff token can point at the whole table with*, since a rate
  limit bounds how many requests a caller makes and not how much each one hands
  over. Portal- and engagement-scoped lists (contacts, meetings, reminders,
  documents, conversations) stay opt-in: the scope is already the bound.

  | Endpoint | Per page | Max via `?page_size=` |
  |---|---|---|
  | `GET /inquiries/` | 10 | 50 |
  | `GET /users/` | 25 | 50 |
  | `GET /event/all` | 7 | 50 |
  | `GET /event/event_day/all` | 7 | 50 |

  **`GET /event/all` and `GET /event/event_day/all` changed shape.** They used
  to return a bare JSON array by default and paginate only when asked; they now
  always return `{count, next, previous, results}`, so a caller moves from
  `res.map(...)` to `res.results.map(...)`. `GET /users/` and `GET /inquiries/`
  already returned `{count, results}`, so for those the envelope only *gained*
  `next`/`previous` — an existing caller still parses the body and simply
  receives one page. Nothing is unreachable in any of the four: `?page=` walks
  the rest, it just costs one request per page, which is the point.
- **Error envelope:** `{ "detail": "...", "code": "machine_code", "errors?": {field: [..]} }`
  on **every** error response project-wide — both exceptions raised via `enforce()`
  / `get_object_or_404` (auto-enveloped by `apps.core.exceptions.custom_exception_handler`)
  and manually-constructed error responses (each `views.py` has a local `_error()`
  helper). Unhandled 500s return `code=internal_error`. Codes are defined in
  `apps/core/error_codes.py`.
- **Two distinct 429s.** `code: "rate_limited"` is a **per-endpoint** limit the
  caller tripped themselves — wait out the window and it clears. `code:
  "throttled_global"` is the **shared ceiling on all anonymous traffic from one
  IP**, which may have been spent by somebody else behind the same NAT, so
  retrying sooner will not help and the message to the user is different. Both
  carry a `Retry-After` header. Branch on `code`, never on `detail`.
- **Two distinct 401s on login.** `code: "invalid_credentials"` means try again;
  `code: "password_reset_required"` means the account has run out of sign-in
  attempts and retrying **cannot** help — the UI must send the user into the
  password-reset flow. Returned identically whether or not the address has an
  account, so it is not a user-enumeration oracle.
- **404 is used for "you may not see this", not just "it does not exist"**, on
  single-object reads where the object's *existence* is itself sensitive —
  currently `GET /users/<email>/`. Returning 403 there would make the status
  code a yes/no oracle: 404 for an unknown address, 403 for a real one, which
  lets any authenticated caller enumerate every account in the system. Callers
  who are entitled to the object (staff, or the user themselves) are unaffected.
- **IDs in URLs are UUIDs**, project-wide, for anything that's a URL path
  parameter — including `EventDay`, `Meeting`, `MeetingPrepItem`, `PrepItemField`,
  and `Conversation` IDs (converted from sequential integers in Phase 6), and
  `InquiryForm`, converted the same way when its staff routes were added.
- **File uploads are capped by size and type**, and a rejection is an ordinary
  field-level 400 (`code: "validation_error"`, the offending field named in
  `errors`) — not a separate error shape. Three ceilings, by what the field
  holds:

  | Ceiling | Bytes | Fields | Accepts |
  |---|---|---|---|
  | photo | 5 MB | `EventContact.photo`, `TeamMember.photo`, `BudgetPayment.receipt` | JPEG, PNG, WebP (+ PDF for the receipt) |
  | image | 10 MB | `EventImage.image` (event + event-day galleries), meeting prep uploads | JPEG, PNG, WebP (+ PDF for prep) |
  | document | 25 MB | the five `document_hub` files (client documents, invoices, receipts, the three portal-default templates) | PDF, JPEG, PNG, WebP |

  The numbers are not arbitrary and the UI should mirror them client-side rather
  than discover them: the web tier runs `gunicorn --timeout 120`, and on a
  congested mobile uplink (~1 Mbps) that budget is exhausted by roughly 15MB —
  so a client-facing upload above the 10MB tier risks being killed mid-transfer
  rather than refused cleanly. Checking size in the browser before sending turns
  a 40-second wait ending in a 400 into an instant, local message. The 25MB tier
  is reachable only on staff-only endpoints, where uploads come from a desk
  rather than a phone. Declared once in `apps/core/uploads.py`.

## Where app-specific behavior is documented

| App | README | What it covers |
|---|---|---|
| `accounts` | `apps/accounts/README.md` | JWT flow, admin-driven registration + forced password change, 3-phase password reset (hashed codes, five-guess budget), login rate limiting + the account lock (failures only, released from the admin), `User.timezone`. |
| `portal` | `apps/portal/README.md` | Portal phase vs. meeting phase, `EventEngagement` lifecycle, default team-member seeding, phase attribution + auto-lock. |
| `events` | `apps/events/README.md` | Event/EventDay model, attribution, the `event_details_updated` durable debounce + sweep, aggregate detail response shape. |
| `contacts` | `apps/contacts/README.md` | Category grouping, day-pinning design, contacts lock. |
| `meetings` | `apps/meetings/README.md` | Prep item/field completion derivation, file-upload validation, atomic nested-field creation. |
| `conversations` | `apps/conversations/README.md` | Tag/link validation, deep-link pills. |
| `reminders` | `apps/reminders/README.md` | Deep-link target registry, email CTA. |
| `document_hub` | `apps/document_hub/README.md` | Auto-seeded portal defaults, percentage-driven payment split, auto-generated reference codes. |
| `documents` | `apps/documents/README.md` | Generic-FK document registry pattern shared across apps. |
| `budgets` | `apps/budgets/README.md` | Budget/category/payment model, receipt registration, Payment History pagination shape. |
| `notifications` | `apps/notifications/README.md` | `queue_notification` dispatch, per-type toggles, the retry/stranded sweep, the five delivery statuses, auth-secret redaction, the cron groups. |
| `inquiries` | `apps/inquiries/README.md` | The one public submission endpoint and how it's protected, the staff triage surface, the two emails and who receives them. |
| `core` | `apps/core/README.md` | Shared deep-link target registry (`apps/core/deeplinks.py`), base models, permissions. |
| *(cross-cutting)* | `docs/RATE_LIMITING_GUIDE.md` | Every rate limit, throttle and lock in the project: the four layers, what each limit is keyed on and why, the single client-IP resolver they all share, and the reCAPTCHA configuration. |
