# API Contract — Inquiries (lead capture)

**Audience: the frontend developer.** This is everything you need to build the
public "plan my event" form and the staff lead inbox against this API. It is
written from the client side: what to send, what comes back, and what to do about
each failure.

Cross-cutting conventions that apply to *every* endpoint in the project (auth,
error envelope, upload ceilings) live in [`API_CONTRACT.md`](./API_CONTRACT.md).
The backend design rationale lives in `apps/inquiries/README.md`. Rate-limit
mechanics live in [`RATE_LIMITING_GUIDE.md`](./RATE_LIMITING_GUIDE.md).

**Base URL:** every path below is relative to `/api/v1/`.
**Trailing slashes are mandatory.** A slashless POST is not redirected.

---

## 1. The one public endpoint

```
POST /api/v1/inquiries/
```

No token. No cookie. **JSON only** — `Content-Type: application/json`.

> **Do not send `multipart/form-data`.** The burst rate limiter reads the email
> out of the raw JSON body to build its bucket key; a multipart post yields an
> empty email and silently collapses that limiter to IP-only bucketing. It will
> appear to work and will meter your users more harshly than intended.

This is the only endpoint in the entire API where an anonymous caller creates a
record, so it is the only one with its own bespoke protections. Everything in §2
and §3 exists because of that.

### 1.1 · Request body

| Field | Type | Required | Constraints |
|---|---|---|---|
| `first_name` | string | ✅ | 1–255 chars, non-blank |
| `last_name` | string | ✅ | 1–255 chars, non-blank |
| `email` | string | ✅ | valid email, ≤255 |
| `phone_number` | string | ✅ | ≤**20** chars, non-blank. See the E.164 note below |
| `contact_mode` | enum | ✅ | `"Email"` \| `"Phone Number"` |
| `event_type` | enum | ✅ | see §1.2 |
| `preferred_start_date` | `YYYY-MM-DD` | ✅ | **today or later** |
| `preferred_end_date` | `YYYY-MM-DD` | ✅ | **≥ `preferred_start_date`** |
| `desired_location` | string | ✅ | 1–255 chars, non-blank |
| `budget` | decimal string \| `null` | ➖ | ≤ `999999999999.99` (14 digits, 2dp). `null` is the "not sure yet" answer |
| `details` | string | ✅ | 1–**4000** chars, non-blank |
| `recaptcha_token` | string | ⚠️ | see §2 — **required in production**, ignored locally |

**Everything except `budget` is required, and `null` is not an accepted value for
any of them.** Six of these fields are nullable in the database (for admin/shell
entry and historical rows) but the API pins them required — a half-filled lead is
a lead staff cannot act on. Sending `"details": null` or `"contact_mode": ""` is a
400, not a silently-stored blank.

**`budget`**: send it as a **string** (`"45000000.00"`) or omit the key entirely.
Do **not** send `0` or `-1` as a "not sure" sentinel — send `null` or omit. A
sentinel would poison budget reporting permanently.

**`phone_number`**: E.164 (`+2348012345678`) is the documented convention and
what the staff-facing UI expects to display. **Nothing enforces it** — the field
is a bare 20-char string. Two consequences that are yours to handle:

- Your country-flag selector determines the stored shape. Be consistent.
- The double-submit dedupe (§3) fingerprints the *whole payload*, so
  `+2348012345678` and `080 1234 5678` are **different submissions**. If your
  input reformats the number between the first click and a retry, the retry
  writes a second lead instead of being collapsed.

### 1.2 · Enum vocabularies — send the **value**, render the **label**

`event_type` is shared verbatim with the `Event` model, so a converted lead maps
onto a real event with no translation table. Three of the five have a label that
differs from the value:

| Send this value | Display this label |
|---|---|
| `"Birthday"` | Birthday Party |
| `"Wedding"` | Wedding |
| `"Corporate"` | Corporate Event |
| `"Social Events"` | Social Events |
| `"Others"` | Others |

`contact_mode`: `"Email"` and `"Phone Number"` — value and label are identical.

**Do not hardcode these maps on the read side.** Every staff-facing response pairs
each choice field with a `_display` sibling (`event_type_display`,
`contact_mode_display`, `status_display`) computed server-side, so a label change
propagates without a frontend deploy. Hardcode only the *submit* dropdown, where
you need the values.

### 1.3 · Responses

**`201 Created`**

```json
{ "detail": "Your inquiry has been received. We'll be in touch within 2 business days." }
```

**That is the entire body. There is no `id` and no echo of what was stored, and
that is deliberate** — reflecting stored data back from an unauthenticated
endpoint would hand an attacker a confirmation oracle. Do not build a UI that
needs a returned id.

A deduped double-submit (§3) returns the **identical** 201. You cannot tell the
two apart, by design.

Show your success state and your own "a confirmation email is on its way to
`<the address they typed>`" line from local form state. The confirmation email
itself deliberately does not repeat the address back.

**`400 Bad Request`** — the standard envelope:

```json
{
  "detail": "Invalid inquiry.",
  "code": "validation_error",
  "errors": { "preferred_start_date": ["The start date cannot be in the past."] }
}
```

Render `errors` field-by-field. Server-side messages worth mirroring client-side
so the user never round-trips for them:

| Trigger | Field in `errors` |
|---|---|
| missing / blank / null required field | that field |
| `event_type` or `contact_mode` outside the vocabulary | that field |
| `preferred_end_date` < `preferred_start_date` | `preferred_end_date` |
| `preferred_start_date` before today | `preferred_start_date` |
| `details` over 4000 chars | `details` |

**A failed reCAPTCHA is also a 400**, but with **no `errors` key**:

```json
{ "detail": "reCAPTCHA verification failed.", "code": "validation_error" }
```

Branch on the absence of `errors` to distinguish "fix your input" from "we could
not verify you're human" — the remedy is completely different (§2.4).

**`429 Too Many Requests`**

```json
{ "detail": "Rate limit exceeded. Please try again later.", "code": "rate_limited" }
```

Always accompanied by a `Retry-After` header carrying **the real wait in
seconds**, not a fixed guess. See §4.

**`500`** — `{"detail": "...", "code": "internal_error"}`. Retry is safe: the
dedupe claim is released on failure, so your retry is treated as a fresh
submission rather than swallowed as a duplicate.

---

## 2. reCAPTCHA v3 — minting and sending the token

The backend runs **reCAPTCHA v3**, not v2. There is no checkbox and no
"I'm not a robot" widget. Your job is to mint a token invisibly at submit time
and put it in the payload.

### 2.1 · Load the script

```html
<script src="https://www.google.com/recaptcha/api.js?render=YOUR_SITE_KEY"></script>
```

The **site key** is public and belongs in the frontend. The **secret key** is
backend-only (`RECAPTCHA_SECRET_KEY`) and must never appear in your bundle.

**One key pair covers every public form on the domain.** That is how v3 is meant
to be used — its score model calibrates on the traffic it sees, and splitting a
small site across two keys leaves both models with less to learn from. What keeps
that safe is the `action` check in §2.2, not key separation.

### 2.2 · Mint a token with the exact action string

```js
const token = await grecaptcha.execute(SITE_KEY, { action: "submit_inquiry" });
```

> ### ⚠️ `action` must be exactly `"submit_inquiry"`
>
> The backend asks Google which action the token was minted for and rejects the
> submission if it doesn't match. This is the **only** thing preventing a token
> harvested from one page being replayed against a different endpoint on the same
> site key — so it is enforced, not advisory.
>
> A typo on either side reads as a replayed token and rejects **every single
> submission** with the reCAPTCHA 400 from §1.3. There is no partial failure mode
> and no warning: it works or nothing gets through. The backend quotes the string
> from a constant (`ACTION_SUBMIT_INQUIRY`); quote it from a constant on your side
> too.

### 2.3 · Mint at submit time, not on page load

**v3 tokens expire in roughly 2 minutes.** Minting on page load and sending
whatever you got when the user finally clicks is the single most common way this
integration breaks — a user who spends three minutes writing their `details`
sends a dead token and gets a rejection they cannot act on.

Call `grecaptcha.execute()` **inside your submit handler**, immediately before the
`fetch`. Wrap it in `grecaptcha.ready()` on first use.

**Tokens are single-use.** If a submit fails for *any* reason — a validation 400,
a 429, a network error — and the user retries, **mint a fresh token**. Re-sending
the same one reads as a replay and is rejected. (The backend recognises a retry as
the same lead regardless: `recaptcha_token` is explicitly excluded from the dedupe
fingerprint, precisely so a fresh token doesn't make a retry look like a new
submission.)

### 2.4 · What a reCAPTCHA rejection means, and what to do

The user gets no signal from Google that they scored low — v3 is invisible. So a
rejection arrives as an ordinary 400 with a message that is deliberately vague.
The backend never tells the caller *why* (score? action? expired?), because that
would be a tuning oracle for whoever is probing it. The detail is in the server
logs only.

**Recommended UX:** treat it as "we couldn't verify this submission" with a retry
affordance, plus a fallback contact route (an email address or phone number) so a
false-positive human is never left with no way to reach the business. Do **not**
show a raw "reCAPTCHA failed" string — most users hitting this are real people on
a VPN or a locked-down browser.

### 2.5 · Behaviour you can rely on

| Situation | Backend behaviour |
|---|---|
| No secret configured (local dev, CI) | Verification is **skipped entirely**. You can omit `recaptcha_token` and everything works. This is why your local setup needs no key |
| Google is unreachable / times out (5s) | **Fails open** — the submission is accepted. Losing a real lead to a Google outage is worse than accepting a spam one, and the rate limits still apply |
| Token missing while a secret *is* configured | **400.** Treat `recaptcha_token` as required in every deployed environment |
| Score below the threshold | 400 |
| Wrong or missing `action` | 400 |

**Current threshold: the inquiry action is in monitor mode (`0.0`).** Every token
is still verified and the `success`/`action` checks still reject, but nothing is
turned away on score while the reCAPTCHA console accumulates a real distribution
for this site. **This will be raised.** Build the rejection path now — do not
assume score rejections never happen because they currently don't.

---

## 3. Double-submit, and what the frontend owes

The backend collapses an **identical** repeat submission inside a **120-second**
window into one lead and one pair of emails. The second request gets the same 201
with no second row.

Two things about that you need to design around:

1. **"Identical" means byte-identical across every field.** A resubmit with one
   corrected character — a fixed date, a reformatted phone number, an extra
   sentence in `details` — is a **new lead**, deliberately. The failure directions
   are asymmetric: a duplicate row costs staff one email they can delete, a lost
   correction is gone. So the backend errs toward saving.

2. **The window is not a substitute for disabling your submit button.** It
   collapses the *row and the emails*, but a genuine double-click firing
   100–300ms apart can still be counted twice by the rate limiter, because the
   dedupe marker is only written after the first request finishes. The burst
   allowance is sized to survive that (a lead gets at least 3 submissions even in
   the pessimistic case) — but it is your button-disabling that makes it a
   non-issue.

**Do this:** disable the submit control on click, re-enable on response. It is the
primary fix; everything the backend does here is the safety net behind it.

---

## 4. Rate limits, from the caller's side

Two tiers apply to `POST /inquiries/`. You never see which one fired — both
return `code: "rate_limited"` — but the `Retry-After` header tells you which in
practice, and it is accurate rather than a fixed 60.

| Tier | Rate | Counted per | The user this catches |
|---|---|---|---|
| burst | 6 per 10 minutes | (IP, email) | one person resubmitting because nothing visibly happened |
| flood | 10 per hour | IP alone | a script varying the email on every request |

**Reading `Retry-After`:** a value in the low hundreds of seconds is the 10-minute
burst window; a value in the thousands is the hourly tier. When both are full, the
**larger** wait is reported — so respecting the header is always sufficient and
you never need to retry to discover a longer wait.

**This endpoint is exempt from the project-wide anonymous ceiling.** It is the
only one in the API that is. A burst of failed logins from the same office cannot
refuse a genuine lead — the two are accounted separately, so you never need to
explain a 429 here as "someone else's traffic".

**Shared connections are the realistic false positive.** An office, a hotel, a
co-working space or a mobile carrier gateway is one IP to this API. Ten inquiries
an hour from one building is the ceiling. If that is a real scenario for a client,
the limit is env-tunable server-side without a deploy — flag it rather than
working around it.

**Recommended handling:** show the wait from `Retry-After` in human terms
("please try again in about 8 minutes"), keep the form contents populated, and
offer the fallback contact route. Never auto-retry a 429 on a loop.

---

## 5. The staff surface

All four routes require a JWT **and** staff/admin role. A client token gets
`403 permission_denied`. There is deliberately no public read, no update of
submitted fields, and no delete — what a lead typed is immutable, and leads are
business records.

```
GET   /api/v1/inquiries/                 the lead inbox
GET   /api/v1/inquiries/summary/         per-status pipeline counts
GET   /api/v1/inquiries/<uuid>/          one lead, every stored field
PATCH /api/v1/inquiries/<uuid>/status/   the only writable field
```

`<uuid>` is a UUID. A non-UUID segment (`/inquiries/5/`) doesn't match the route
at all and returns 404 from the URLconf.

### 5.1 · `GET /inquiries/` — the lead inbox

Query parameters, all optional:

| Param | Values | Notes |
|---|---|---|
| `status` | any status value (§5.4) | exact match |
| `event_type` | any event type value (§1.2) | exact match |
| `search` | free text | case-insensitive `contains` across **first name, last name, email, phone number, desired location**. Deliberately the same field set the Django admin searches |
| `ordering` | see below | prefix `-` for descending. Default `-created_at` |
| `page` | integer | |
| `page_size` | integer, max **50** | default 10 |

**`ordering` is allow-listed.** Permitted: `created_at`, `preferred_start_date`,
`status`, `event_type`, `last_name`. Anything else is a **400 with the allowed
list in `detail`** — not a silent fallback, so a typo surfaces immediately.

**Always paginated.** There is no unpaginated mode; `?page=` walks the rest.

```json
{
  "count": 42,
  "next": "https://api.example.com/api/v1/inquiries/?page=2",
  "previous": null,
  "results": [ /* InquiryRead objects — §5.3 */ ]
}
```

**10 per page**, not the 7 used elsewhere in the portal. Don't assume a shared
page size across the API — read `count` and follow `next`.

### 5.2 · `GET /inquiries/summary/` — pipeline counts

For a dashboard's status columns, so you never fetch every lead to count them.

```json
[
  { "status": "new",       "status_display": "New",       "count": 12 },
  { "status": "contacted", "status_display": "Contacted", "count": 5  },
  { "status": "qualified", "status_display": "Qualified", "count": 0  },
  { "status": "converted", "status_display": "Converted", "count": 3  },
  { "status": "lost",      "status_display": "Lost",      "count": 1  },
  { "status": "archived",  "status_display": "Archived",  "count": 0  }
]
```

- **Every status is always present, including zeros**, so your pipeline columns
  don't appear and disappear as leads move. Render straight from this array.
- Honours `?event_type=` and `?search=` so the tallies match what the same filters
  would return from the list.
- **`?status=` is ignored** — filtering a per-status tally by status answers
  nothing. `?ordering=` and `?page=` are meaningless here; no lead rows come back.

### 5.3 · The read shape (`InquiryRead`)

Returned by the list, the detail route, and the status PATCH. **Entirely
read-only.**

```json
{
  "id": "3f1c…",
  "first_name": "Ada",
  "last_name": "Okoye",
  "email": "ada@example.com",
  "phone_number": "+2348012345678",
  "contact_mode": "Email",
  "contact_mode_display": "Email",
  "event_type": "Wedding",
  "event_type_display": "Wedding",
  "preferred_start_date": "2027-06-01",
  "preferred_end_date": "2027-06-03",
  "desired_location": "Lagos, Nigeria",
  "budget": "45000000.00",
  "details": "Expecting 250 guests…",
  "status": "contacted",
  "status_display": "Contacted",
  "created_at": "2026-08-30T09:12:44Z",
  "created_by_display": "",
  "updated_at": "2026-08-30T11:02:10Z",
  "last_updated_by_display": "Winnie Adeyemi"
}
```

- **`created_by_display` is always `""`.** Leads arrive unauthenticated, so there
  is no creator to name. It is carried anyway so the attribution block reads
  identically to every other model in the API. Don't render an empty "Created by".
- **`last_updated_by_display` means "who moved this lead"**, because `status` is
  the only mutable field. Pair it with `updated_at` for the "Last updated by … on
  …" line. Resolved at read time, so a staff rename propagates.
- **`budget` is a string**, or `null` when the lead chose "not sure yet". Format
  the ₦ and thousands separators yourself.
- Raw actor FK ids are stripped — you get the `_display` strings only.

### 5.4 · `PATCH /inquiries/<uuid>/status/` — triage

```json
{ "status": "contacted" }
```

Returns the full `InquiryRead` object. Statuses: `new`, `contacted`, `qualified`,
`converted`, `lost`, `archived`.

**Transitions are validated.** Build your status dropdown from this table rather
than offering all six and handling the 400:

| From | May move to |
|---|---|
| `new` | `contacted`, `qualified`, `lost`, `archived` |
| `contacted` | `qualified`, `converted`, `lost`, `archived` |
| `qualified` | `converted`, `contacted`, `lost`, `archived` |
| `converted` | `archived` *(only)* |
| `lost` | `contacted`, `archived` |
| `archived` | `new` |

The shape of that table, in words: skip-ahead is allowed (a lead who phones and is
obviously qualified goes straight `new → qualified`), `converted` is near-terminal
because conversion will create a user and an event, `lost` is revivable because
"they came back six months later" is a real workflow, and `archived` keeps one
exit so a mis-click is never permanent.

**Two different failures, two different codes** — branch on `code`:

| Body | Meaning |
|---|---|
| `{"detail": "Invalid status. Allowed: …", "code": "validation_error"}` | not a status at all (a typo). The value check runs **first**, so a typo never misreports as an illegal transition |
| `{"detail": "Cannot move a lead from 'converted' to 'new'.", "code": "invalid_transition"}` | a real status, but not a legal move from here |

**Re-sending the status a lead already has is an accepted 200 no-op**, not a 400 —
so a double-click on your status control is safe. It still writes, refreshing
`last_updated_by` / `updated_at`.

---

## 6. What happens after a successful submit

Two emails go out, neither of which you control or need to reflect:

| Email | To | Contents |
|---|---|---|
| `inquiry_received` | the lead | **One parameter: their first name.** Static copy, hardcoded CTA. It deliberately echoes back no event type, no dates, no location, no budget — and not their email address either. The "a confirmation has been sent to `x@y.com`" line belongs on **your** success page |
| `inquiry_submitted_internal` | each flagged staff member | the full lead detail, one email per recipient |

**Staff alerts are opt-in per account and default to off.** If nobody has the flag
ticked, the lead still saves and the client still gets their acknowledgement — the
submission has not failed — but nobody is told. The only trace is a server-side
`inquiry_no_recipients` log event. Nothing about this is visible to your code; it
is listed so you don't chase a "the form works but nobody got the email" report as
a frontend bug.

---

## 7. Integration checklist

- [ ] `Content-Type: application/json` — never `multipart/form-data`
- [ ] Trailing slash on `/api/v1/inquiries/`
- [ ] Site key in the frontend; **secret key never in the bundle**
- [ ] `grecaptcha.execute()` called **inside the submit handler**, not on page load
- [ ] Action string is exactly `"submit_inquiry"`, quoted from a constant
- [ ] A **fresh** token minted on every retry
- [ ] Submit button disabled on click, re-enabled on response
- [ ] `budget` sent as a string or `null` — never `0`
- [ ] All required fields sent as non-empty values; `null` is never valid
- [ ] Dates as `YYYY-MM-DD`; start ≥ today; end ≥ start; validated client-side too
- [ ] `details` capped at 4000 chars in the textarea, with a counter
- [ ] Phone number formatted consistently (E.164 recommended)
- [ ] 400 with `errors` → field-level messages; 400 without `errors` → the
      captcha path with a fallback contact route
- [ ] 429 → read `Retry-After`, show a human wait, keep the form populated, no
      auto-retry loop
- [ ] Success state driven from local form state — the 201 carries no data
- [ ] Staff UI: read `_display` fields, never hardcode value→label maps
- [ ] Staff UI: status dropdown built from the transition table, not all six
      statuses
