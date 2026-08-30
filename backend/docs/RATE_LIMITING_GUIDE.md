# Rate Limiting — Plain English Guide

A reader's guide to the limits, not a rewrite of them.

**Where the real numbers live.** `config/settings.py` — `RATE_LIMITS` (per-endpoint)
and `THROTTLE_RATES` (project-wide). The *keys* they count against are wired in
`apps/accounts/urls.py`, `apps/inquiries/urls.py` and `apps/accounts/login_guard.py`.
Everything below describes that code; where the two ever disagree, the code is
right and this file is the bug.

**Scope.** This covers the four things that can refuse a request — the identity
resolver, the per-endpoint limits, the project-wide throttles, and the durable
account lock — plus the two adjacent controls people mistake for limits (the
inquiry dedupe window and reCAPTCHA). Open items that were deliberately not fixed
are **not** here: they live in [`INQUIRY_V2_Upgrades.md`](./INQUIRY_V2_Upgrades.md)
**Part B**, so this stays a guide rather than a backlog.

---

## 1. The architecture, in one picture

Four layers plus two adjacent controls. A request passes through them in order.

```
  request
     │
     ├─ Layer 1 · apps/core/ratelimit.py ── "who is this client?"
     │     ONE answer, used by everything below. Reads X-Forwarded-For
     │     right-to-left, masks IPv6 to /64. Nothing else may read REMOTE_ADDR.
     │
     ├─ Layer 2 · django-ratelimit ── PER-ENDPOINT limits (RATE_LIMITS)
     │     Two placements, same library:
     │       (a) wrapped around the view at the URL — token refresh,
     │           the three password-reset steps, inquiry submit
     │       (b) called INSIDE the view — API login, admin login
     │     Blocks → 429  code="rate_limited"
     │
     ├─ Layer 3 · DRF throttles ── PROJECT-WIDE ceilings (THROTTLE_RATES)
     │     Applied to every DRF view by default.
     │     Blocks → 429  code="throttled_global"
     │
     └─ Layer 4 · durable counters ── the ACCOUNT LOCK, no window at all
           User.failed_login_count (a database column) and a second
           email-keyed counter in the cache. Five failures is five failures
           however they are spaced or wherever they came from.
           Blocks → 401  code="password_reset_required"

  adjacent, and NOT limits:
     · inquiry dedupe window  — collapses a double-click into one lead
     · reCAPTCHA v3           — scores the submitter before anything is counted
     · pagination             — bounds one response, not the request rate
```

**Layer 1 is the one that makes the rest coherent.** Before it existed there were
three separate answers to "what is the client IP" and two were wrong. Now
`bucket_ip()` is the single answer for every counter in the project, so no two
layers can disagree about who they are limiting. `resolve_client_ip()` is its
unmasked sibling, used where precision matters rather than bucketing — the
password-reset audit trail, and the `remoteip` sent to Google's reCAPTCHA
siteverify.

Two details of Layer 1 worth knowing:

- **Right-to-left XFF reading is what makes the header safe.** Every proxy in
  both deployment shapes *appends*, so anything a client prepends sits to the
  left of the real address and is never reached. A client who sends
  `X-Forwarded-For: 9.9.9.9` is still bucketed under their own address.
- **IPv6 is masked to /64 for bucketing.** A residential or mobile IPv6
  allocation is a /64 or larger and the client owns every address inside it, so
  an unmasked key would hand them a fresh bucket per request and make every
  IP-keyed limit free to bypass.

**Where the counters live:** the Django cache. In production that must be Redis,
or each gunicorn worker keeps its own counters and every limit is silently 3x.
The app refuses to boot with `DEBUG=False` and no `CACHE_REDIS_URL` — the one
config error in this area that is loud rather than silent.

---

## 2. How to read the names

Once you know the suffix rule, most of the table reads itself:

| Name shape | Keyed on | The question it answers |
|---|---|---|
| `x` (no suffix) | IP prefix | **How fast?** — burst control |
| `x_account` | the submitted email | **How much against one person?** |
| `x_daily` | *usually* the IP prefix | **How much in a whole day?** |

**The one exception, and it matters:** `auth_login_account_daily` is keyed on the
**email**, not the IP — it is the daily sibling of `auth_login_account`, not of
`auth_login`. Read the `_daily` suffix as "the day-length tier of whatever it is
suffixing", and the rule holds everywhere.

Login is the worked example, because it carries all three axes at once:

- **`auth_login` — 10/m per IP.** *One laptop can't machine-gun the login form.*
- **`auth_login_account` — 10/h per email.** *A thousand laptops can't
  machine-gun **your** account.* This is the axis an IP limit cannot see: a
  credential-stuffing botnet has a fresh IP per request, so the per-IP tier never
  fires. The email doesn't change — so this one does.
- **`auth_login_daily` — 100/d per IP.** *One laptop can't grind slowly all day.*
  10/m sustained is 14,400 attempts a day. The per-minute limit caps a burst and
  nothing else; this puts a roof on the marathon.
- **`auth_login_account_daily` — 50/d per email.** The same roof on the account
  axis. Safe to exist only because the login tiers count *failures* — see §5.

---

## 3. Every limit, and what it's for

### 3.1 · API auth (`apps/accounts`)

| Limit | Rate | Per | Stops |
|---|---|---|---|
| `auth_login` | 10/m | IP | one machine hammering passwords |
| `auth_login_account` | 10/h | email | a distributed botnet targeting one account |
| `auth_login_daily` | 100/d | IP | slow all-day grinding from one machine |
| `auth_login_account_daily` | 50/d | email | the same, on the account axis |
| `token_refresh` | 30/m | IP | a frontend stuck in a refresh loop |
| `token_refresh_daily` | 500/d | IP | — (deliberately loose, see below) |
| `password_reset_request` | 3/h | (IP, email) | email-bombing one person's inbox |
| `password_reset_request_daily` | 20/d | IP | one machine bombing many inboxes |
| `password_reset_verify` | 10/m | IP | rapid guessing of the 6-digit code |
| `password_reset_verify_daily` | 50/d | IP | slow all-day code guessing |
| `password_reset_confirm` | 10/m | IP | hammering the final set-password step |
| `password_reset_confirm_daily` | 20/d | IP | same, over a day |

Three footnotes worth knowing:

- **`token_refresh_daily` at 500/d is the loosest by a distance, on purpose.**
  Access tokens last **1 hour**, so an active session refreshes ~9 times a
  workday. A dozen staff behind one office IP is ~110 legitimate refreshes. It is
  also the least useful endpoint to attack: refresh tokens rotate and blacklist,
  so a stolen one works exactly once.
- **`password_reset_request` is the only auth limit keyed on `(IP, email)`.**
  That pairing is right *here* because the email must belong to a real account
  for anything to happen — so varying it buys nothing. On a public form where the
  email is free (the inquiry endpoint), that same shape would be worthless.
- **The four login tiers count FAILED attempts only.** They are checked on the
  way into the view and incremented on the way out, and only when authentication
  actually failed. A correct sign-in costs nothing, so an office of any size can
  log in at once. See §5.

### 3.2 · The Django admin's own login (`apps/core/admin_login.py`)

`/admin/login/` is not a DRF view and is not wrapped by the decorators in
`apps/accounts/urls.py`, so until these existed it had **no limit of any kind** —
unlimited password guesses against any `is_staff` account, and an account locked
out of the API could still walk in through the admin. Since `role=admin` forces
`is_superuser=True`, that is the whole control plane.

| Limit | Rate | Per | Notes |
|---|---|---|---|
| `admin_login` | 5/m | IP | its **own** IP group, so operator traffic and public API traffic never draw down each other's buckets |
| `admin_login_daily` | 50/d | IP | same |
| `auth_login_account` | 10/h | email | **shared with the API on purpose** — one account under attack is one account, whichever door is being tried |
| `auth_login_account_daily` | 50/d | email | shared, same reason |

Tighter per-minute than the API's 10/m because the admin has a handful of human
users and no programmatic callers: nothing legitimate here retries, so a burst is
a guesser. **Failed POSTs only** — a GET of the form is never counted, or a page
refresh would spend an operator's allowance.

It is wired as a **URL override**, not an `AdminSite` subclass: `config/urls.py`
lists `admin/login/` *before* `admin/`, Django resolves in order, so this view
wins and `admin.site.urls` never sees the request. `reverse('admin:login')` still
works and still lands here, so every "you must log in first" redirect is covered.

### 3.3 · Inquiry (`apps/inquiries`) — public lead capture

| Limit | Rate | Per | Stops |
|---|---|---|---|
| `inquiry_submit_burst` | 6/10m | (IP, email) | a real person impatiently resubmitting |
| `inquiry_submit_ip` | 10/h | IP | a script, regardless of what email it types |
| *dedupe window* | 120s | payload fingerprint | *(not a limit)* a double-click becoming two leads + two emails |

**The IP tier is the load-bearing half.** A single `(IP, email)` limit looks
strict and isn't: the email on a public form is attacker-chosen and free, so
varying it buys a fresh bucket every time and one machine can submit without
limit. The burst tier is what a fumbling human hits; the IP tier is what a script
hits.

**Why the burst count is 6:** a double-click costs either one attempt or two
depending on a race, so the number has to work under both readings.

`dedupe.burst_rate` is a **callable** rate, not a string. It returns `None` for a
payload the dedupe window has already accepted, and `None` makes django-ratelimit
skip the check *without incrementing* — so a repeat arriving after the first
request finished is free. But the marker is written at the *end* of the view,
after validation, the reCAPTCHA round-trip and the insert. A real double-click
fires ~100–300ms apart, so the second request can reach the rate callable while
the first is still in flight, find no marker, and get counted.

The arithmetic therefore has to hold both ways:

| | a lead gets |
|---|---|
| race bites (double-click = 2) | **3 submissions** |
| dedupe catches it (= 1) | **6 submissions** |

**What this number does *not* bound:** one `(IP, email)` gets six of these
10-minute windows inside one `inquiry_submit_ip` hour, so a single submitter can
reach that 10/h ceiling at *any* burst value. The burst count changes how fast
they get there (two windows at 6, three at 4), not how much they can take.

**Only the burst tier skips a duplicate.** The flood tier counts every request
that arrives, so replaying one identical payload forever is still bounded even
though each replay writes no row and sends no email.

This endpoint is also the **only** one in the project that opts out of the
project-wide `anon` ceiling (`@throttle_classes([])`). Reason: `anon` is one
shared pool per IP across *all* anonymous endpoints, so a burst of failed logins
from an office could have refused a genuine lead from that same office. It has
two limits chosen specifically for it; it doesn't need a third that isn't.

### 3.4 · Project-wide (`apps/core/throttling.py`)

| | Value | What it's for |
|---|---|---|
| `anon` | 1000/day per IP | **safety net only** — see below, it is currently unreachable. Exists for a public endpoint nobody remembered to wire a limit onto. |
| `user_burst` | 120/m per account | **the only limit on logged-in traffic.** Catches a frontend stuck in a retry loop. No human can reach it. |
| near-limit signal | 80% of ceiling | logs `event="throttle_near_limit"` *before* anyone is refused — a leading signal, not a lagging one |

Both classes take their identity from Layer 1 rather than DRF's own `get_ident`,
which with `NUM_PROXIES` unset uses the **entire** `X-Forwarded-For` string as
the bucket identity. That gave three defects at once: prepending one address
yielded unlimited fresh buckets, the proxy's own address sat inside the key so
every bucket reset when the platform edge rotated, and with no XFF at all every
anonymous request in the world collapsed into one bucket.

**`anon` cannot actually fire today, and that is the design.** It covers the five
anonymous auth endpoints in one shared per-IP bucket — and each of those already
has its own daily cap. They sum to less than the ceiling:

```
auth_login_daily                100
token_refresh_daily             500
password_reset_request_daily     20
password_reset_verify_daily      50
password_reset_confirm_daily     20
                              -----
                                690   ← max anonymous requests/day from one IP
anon ceiling                   1000
```

Max out all five and you stop at 690. So `anon` is not a limit anyone reaches; it
is the floor under a *future* endpoint that ships without its own limits.

**The inquiry endpoint is not in that pool at all** (§3.3), so its 10/h is
completely separate accounting — max 240/day from one IP — and spending it draws
nothing from the 1000/day.

Two mechanics that follow from the ordering: the URL-wrapped limiters run
**before** DRF's throttle, so a request blocked with a 429 never reaches `anon`
and doesn't consume it. And `anon` only counts anonymous requests;
`get_cache_key` returns `None` once you are authenticated, at which point
`user_burst` takes over.

**Why `user_burst` is per-minute and not per-day:** every account here is created
by staff — there's no public signup — so a misbehaving *human* gets switched off
with `set_user_status`, not rate-limited. The only thing a limit is good for on
this surface is a runaway *client*, and a daily budget handles that as badly as
possible: the loop burns the whole day in seconds, then locks a real person out
for hours. A per-minute ceiling stops the loop in about a second and clears
itself within the minute.

`UserBurstRateThrottle.get_cache_key` returns `None` for an anonymous request
rather than falling back to an IP bucket, which is what `UserRateThrottle`
inherits by default. The scope is *defined* as the per-account ceiling; an
anonymous request has no account to meter and is already covered by `anon` plus
its endpoint's own limits.

---

## 4. The stacking rule (the bit that's easy to get wrong)

For the **URL-wrapped** limits, **the outermost tier counts first and raises
first — so an inner tier never counts a request the outer tier already refused.**

The order everywhere is: **burst → narrowest axis → daily backstop.** Reverse it
and a few seconds of rapid retries would eat a whole day.

**Login is different, and deliberately.** Because its tiers are checked in the
view rather than nested as decorators, *every* tier is checked before the attempt
and *every* tier is counted after a failure. Nesting no longer shields one tier
from another — which is fine here, because the thing being counted is already
narrowed to "an attempt that actually failed".

**Every limit also declares an explicit `group=`.** This is not cosmetic. Left to
itself, django-ratelimit derives the group from the view's module + qualname —
and *every* `as_view()` result has the qualname `View.as_view.<locals>.view`. So
two endpoints in one module with the same rate and key silently share one bucket.
That actually shipped: `password_reset_verify` and `password_reset_confirm` are
both 10/m on IP in the same module, and a user who used up their verify attempts
couldn't then confirm. `RateLimitGroupIsolationTests` now pins that no two limits
can collide.

---

## 5. The account lock — the control that isn't a rate limit

Rate limits smooth; **this** is what stops guessing. It has no window, so five
failures is five failures however they are spaced and wherever they came from.

Two counters, and they are not redundant:

| Counter | Lives in | Covers |
|---|---|---|
| `User.failed_login_count` / `failed_login_at` | a **database column** | addresses that have an account. Durable, survives a cache eviction, visible in the admin when triaging "the client says they can't sign in" |
| `login_failures:<sha256(email)>` | the **cache** | *every* submitted address, account or not |

Ceiling is `User.MAX_FAILED_LOGINS = 5`, ageing out after
`FAILED_LOGIN_WINDOW = 24h`. **Either** counter at the ceiling means locked, so a
cache eviction can only ever lose the weaker of the two.

**Why the second counter exists: it closes a user-enumeration oracle.** The
account counter can only exist for an address that *has* an account. If the "you
are out of attempts, go and reset" response were driven by it alone, that
response would itself answer *"does this address have an account here?"* — five
wrong passwords and you know. The email-keyed counter is incremented whether or
not anything is behind the address, so a real address and an invented one become
indistinguishable at the same threshold. It is hashed, not raw: cache keys
surface in Redis tooling and dashboards, and an inventory of every email ever
typed at a login form is not something to leave lying there.

**The lock is checked *before* the password.** It has to be, or the ceiling
bounds nothing — and verifying first would spend ~68ms of PBKDF2 per guess. The
cost is that a correct password cannot rescue a locked account.

**Recovery — two paths, and both must stay working.** The admin's own "Release
login lock" action is *inside* the admin, so a lock applied at `/admin/login/`
would otherwise be the release button behind the locked door:

1. **`manage.py release_login_lock <email>`** — the break-glass. It must run
   against the *production* env: two of the three things holding a lock are in
   Redis, so a run against a local cache clears the database column and leaves the
   cache counters holding the lock. On Render that means `render ssh` or the
   dashboard Shell — **both paid-tier only**, so on the free plan this command is
   unavailable in production and the admin action is the only route.
2. **The password-reset flow**, which clears the lock on completion and delivers
   its code to an inbox an attacker cannot read.

`login_guard.release_account()` is what both the command and the admin action
call, so they cannot drift. It clears the failure counter **and** both
account-keyed rate tiers — a release that cleared only one would appear to work
and then refuse the very next attempt. It deliberately does **not** touch the
IP-keyed tiers: those are a property of a machine, not an account, and clearing
them from a user row would silently unblock whoever else is behind that address.

Superusers are **not** exempt. They are the highest-value target; the two
recovery paths are what make that safe rather than reckless.

**Residual, and accepted:** an attacker can force an account holder to complete a
password reset. That is the standard trade for having any per-account bound.

### The reset code is defended the same way

The `password_reset_verify` 10/m tier looks like the guard on the six-digit code.
It is the outer layer, not the main one. Three controls stack:

| Control | Lives on | Bounds |
|---|---|---|
| `password_reset_verify` 10/m + 50/d | rate limit, per IP | how fast **one source** guesses |
| `MAX_VERIFY_ATTEMPTS = 5` | the token row | total guesses per issued code, **from any number of IPs** |
| `RESET_CODE_TTL_MINUTES = 30` | the token row | how long the code is alive at all |

The middle one closes the distributed case. Six digits is a 10⁶ search space and
an IP-keyed limit can always be spread across more IPs — but the counter lives on
the token, so five wrong guesses burn that code wherever they came from. (It is
recorded via `attempts_exhausted()` rather than `is_used=True`, so the row stays
visible to the lookup and the user is told to request a new code instead of
getting a generic "invalid or expired".)

**One constraint runs the other way: the 10/m limit is what makes the hashing
affordable.** The code is stored as PBKDF2 via `make_password`, not a bare digest
— SHA-256 of six digits is a sub-second rainbow table. That costs **~68 ms of CPU
per verify** (measured, Django's default PBKDF2 hasher). At 10/m it's nothing. At
600/m it's a CPU-exhaustion vector. This is the one limit in the project whose
ceiling is set by compute cost as well as by security — don't raise
`RATE_LIMIT_PASSWORD_RESET_VERIFY` without that in mind.

---

## 6. What a 429 actually tells the caller

**Two distinct 429s, and the remedy differs**, so a frontend branches on `code`
and never on `detail`:

| `code` | Raised by | Means |
|---|---|---|
| `rate_limited` | django-ratelimit → `RATELIMIT_VIEW` (`apps.core.views.ratelimited`) | a **per-endpoint** limit this caller tripped themselves. Wait out the window and it clears. |
| `throttled_global` | DRF's `Throttled` → `custom_exception_handler` | the **shared ceiling on anonymous traffic from one IP**, which may have been spent by somebody else behind the same NAT. Retrying sooner will not help, and the message to the user is different. |

**`Retry-After` carries the real wait, not a flat guess.** It used to be a flat 60
on every django-ratelimit 429, which is a lie on a daily cap: a client that
respects the header retries every minute for up to 24 hours, is refused each
time, and looks broken while filling the logs. The middleware is handed only the
exception, and django-ratelimit raises a bare `Ratelimited()` carrying nothing —
so `time_left` reaches the renderer by two routes, in order of preference:

1. **`exception.retry_after`** — set by a caller that already knows. Both login
   views check their tiers themselves, so they have the answer in hand.
2. **`request.rate_limit_tiers`** — stashed by the `_rl` helpers *before* the
   decorators run, letting `apps/core/views._retry_after` re-check the tiers with
   `increment=False` and find the full one.

Stashed on the **request**, not as an attribute on the view function: `resolve()`
on `/inquiries/` returns the dispatcher, not the wrapped POST handler, so a
view-attribute lookup would miss exactly one endpoint.

**The largest wait wins**, not the first — an endpoint can be over two ceilings at
once, and reporting the burst's 60 seconds while the day is also full guarantees
the next attempt is refused too. `RATELIMIT_RETRY_AFTER_SECONDS` (60) survives as
the last-resort fallback rather than the only answer.

**A comparison detail that is easy to get backwards.** `first_full_tier` (the
in-view check) uses `count >= limit`, because nothing has been counted yet —
`>` would admit one attempt beyond the ceiling on every tier. `_retry_after` (the
post-block renderer) uses `count > limit`, because the decorator already counted
the request being refused, so a tier sitting exactly *at* its limit still had room
for it and is not the one that fired.

### What gets logged

| Event | Level | Emitted by | Says |
|---|---|---|---|
| `rate_limited` | WARNING | `core/views.ratelimited` | path, method, resolved client IP, `retry_after` |
| `throttle_near_limit` | WARNING | `core/throttling` | a bucket hit 80% — the leading signal, before anyone is refused |
| `login_tier_exhausted` | INFO | `accounts/views` | **which** of login's four tiers fired |
| `admin_login_rate_limited` | WARNING | `core/admin_login` | same, for the admin door |
| `admin_login_account_locked` | WARNING | `core/admin_login` | the lock refused a sign-in; `has_account` separates the two cases |
| `login_account_locked` | WARNING | `accounts/views` | the API equivalent |
| `client_ip_unresolved` | WARNING | `core/ratelimit` | **the edge stopped sending XFF**, so every client now shares one bucket. Must be zero. |

Per `docs/OBSERVABILITY_STANDARD.md` the app emits and the stack decides; there
is no alerting in this code. The catalogue and the Grafana rules are in
[`observability/`](./observability/README.md).

---

## 7. reCAPTCHA — the layer above the limits on the public form

Rate limits bound *volume*. reCAPTCHA is the only thing that looks at *who is
submitting*, and it applies to exactly one endpoint: `POST /inquiries/`.

**v3, not v2, and the difference is the whole integration.** A v2 checkbox token
is a challenge a human solved, so `success: true` **is** the verdict. A v3 token
is minted silently by JavaScript, and there `success: true` only means "this
token parsed, has not expired, has not been redeemed, and was issued for this
site key" — every bot driving a headless browser gets one. The verdict is
`score`, so a v3 integration reading only `success` accepts everything.

Four checks, in `apps/inquiries/recaptcha.py`:

| Check | Why |
|---|---|
| `success` | the token is well-formed, unexpired and unredeemed |
| `action` | **one site key covers every form on the domain**, so this is the only thing stopping a token harvested from a cheap public page being replayed against a more valuable endpoint. Any new caller must pass its own action and register a threshold. |
| `score ≥ threshold` | the actual bot verdict. Per-action, from `RECAPTCHA_MIN_SCORES` |
| *(no hostname check)* | deliberate — the key's domain allowlist in the reCAPTCHA console already enforces it, and duplicating it in code is one more place to edit when a second domain is added |

**Three behaviours to know before you tune it:**

- **Entirely env-gated.** With no `RECAPTCHA_SECRET_KEY` this is a no-op that
  returns `True`, so local dev, CI and tests need no config and boot is never
  blocked. If it is unset in production, the two rate tiers are the *only* bot
  defence on public lead capture.
- **It fails OPEN on any network error**, with a 5-second timeout. Losing a real
  lead to a Google outage is worse than accepting a spam one, and the endpoint is
  rate-limited regardless. *A caller that is not lead capture should re-examine
  that trade* — on an auth endpoint, fail-open means the bot defence silently
  disappears for the duration of an outage.
- **A v2 secret is accepted, loudly.** No `score` field in the response means the
  configured secret belongs to a v2 key. That is a provisioning mistake rather
  than an attack, and for a v2 key `success` genuinely is the verdict — so it
  accepts, but emits `event="recaptcha_v2_key_in_use"` at ERROR, because every
  threshold is then silently doing nothing.

**Current thresholds, and why they are what they are:**

| Setting | Shipped default | `.env.example` | Meaning |
|---|---|---|---|
| `RECAPTCHA_MIN_SCORE_SUBMIT_INQUIRY` | `0.5` | **`0.0`** | **monitor mode.** Every token is still verified — so the console accumulates a real score distribution — and `success` + `action` still reject, but nothing is turned away on score while there is no data to choose a threshold from. |
| `RECAPTCHA_MIN_SCORE_DEFAULT` | `0.5` | `0.5` | applies to any action with no entry of its own. Deliberately **not** 0.0: it governs a future endpoint someone wires up and forgets, and 0.0 there would wave it through unscored with nothing to surface it. |

Raise the inquiry threshold once the reCAPTCHA console shows what genuine traffic
actually scores. Err low: a wrongly rejected inquiry is a lead the business never
learns it had.

Events to watch: `recaptcha_rejected`, `recaptcha_low_score`,
`recaptcha_action_mismatch`, `recaptcha_unreachable`, `recaptcha_v2_key_in_use`.

---

## 8. Adjacent controls people mistake for limits

**The inquiry dedupe window (120s).** It prevents a duplicate *row* and a
duplicate *pair of emails*; it does not limit anyone. Two separate fingerprints
exist and they never need to agree with each other, only with themselves:

- `dedupe._fingerprint` — hashes the **raw JSON body**, computed once in the rate
  callable (outside DRF, where only `request.body` is available) and once in the
  view from `request.data` (DRF has consumed the stream by then, and re-reading
  `request.body` raises `RawPostDataException` — an earlier version did exactly
  that and turned every submission into a 500 whenever `RATELIMIT_ENABLE` was
  false).
- `services._dedupe_key` — hashes the **validated** payload: `Decimal`s quantised
  to 2dp, dates ISO-formatted, `None`s dropped, email lowercased.

It fingerprints the **whole payload, not the email**. An email-only key made it an
*email lockout*: a lead resubmitting 40 seconds later with a corrected date got a
201 and no row, and the correction was destroyed silently. The failure directions
are asymmetric — a duplicate row costs staff one email they can delete, a lost
lead is gone — so the key errs strict.

`cache.add()` is the atomic set-if-absent primitive; a `get()`/`set()` pair would
race the very double-click it defends against. The claim is **released on
failure**, or a submit that 500s would hold the key for the full window and
silently swallow every retry inside it.

Every swallowed submit emits `inquiry_dedupe_hit` — the only trace it leaves, and
the signal to retune the burst count against rather than guessing.

**The window must fit inside the burst window.** `apps/inquiries/tests.py` pins
that relationship, so the two numbers cannot drift apart unnoticed.

**Pagination.** `GET /inquiries/` at 10/page and `GET /users/` at 25/page are
unconditional, and that is a *response-size* control, not a rate limit. The rule
is not "big lists" — it is **any list a staff token can point at the whole table
with**. A rate limit bounds how many requests a caller makes, not how much each
one hands over, so a compromised staff token needs exactly one request. Bounding
the page is the control that actually applies. Portal- and engagement-scoped
lists stay opt-in: the scope is already the bound.

---

## 9. Where to change things

Every number is `env("VAR", default=<value>)` in `config/settings.py`. **The value
in that file is the real declared policy; the env var is a one-deploy escape
hatch for tuning without a code change.**

`.env.example` lists every override name **commented out**, deliberately —
uncommenting one creates a second copy of the number that will drift. (Note
`RATE_LIMIT_INQUIRY_SUBMIT` no longer exists; it was replaced by the two tiers in
§3.3.)

If you change a limit permanently, change it in `config/settings.py`.

Two things that are **not** env-tunable, on purpose: `User.MAX_FAILED_LOGINS` and
`PasswordResetToken.MAX_VERIFY_ATTEMPTS`. Both are 5, matching each other, and
both are security policy expressed in code rather than deploy config.

### Under the test runner

Both layers are neutralised, by different mechanisms, and the difference matters
when you write a test:

- `RATELIMIT_ENABLE = not TESTING` turns django-ratelimit off. Rate-limit tests
  opt back in with `@override_settings(RATELIMIT_ENABLE=True)`.
- DRF's rates become `None`, which makes `allow_request` return `True`
  immediately. Note this nulls the **rates** and leaves `DEFAULT_THROTTLE_CLASSES`
  wired, so every view keeps reporting the ceilings it really carries — which is
  what lets a test asserting "this endpoint opts out of throttling" test
  something instead of being vacuously true everywhere.

`THROTTLE_RATES` is kept as its own setting precisely so the *declared* policy
stays readable under a runner that neutralises the active one.

---

## 10. Known, accepted properties

Not gaps — properties with a stated cost, listed so nobody rediscovers them as
bugs. Anything genuinely open lives in
[`INQUIRY_V2_Upgrades.md`](./INQUIRY_V2_Upgrades.md) **Part B**.

- **django-ratelimit windows are fixed, not sliding.** A 10/m limit permits 10
  requests at 11:59:59 and 10 more at 12:00:00. Read every rate in §3.1–3.3 as
  "roughly 2x this, briefly". The DRF ceilings in §3.4 are *not* affected —
  `SimpleRateThrottle` keeps a list of timestamps and is already sliding-window.
  And the controls that need a hard bound don't use windows at all: the two
  counters in §5 live on a row and in a keyed cache entry.
- **The authenticated surface has a burst limit and nothing else.** `user_burst`
  at 120/m is the whole story for logged-in callers — no hourly cap, no daily cap.
  Correct for the stated threat model (runaway clients, not bad humans). Two
  consequences to be aware of: a compromised staff token can make 120 requests a
  minute for as long as it is valid (the control is revocation, not throttling),
  and `POST /users/register/` sends an email, so a compromised staff account could
  send 120 emails a minute.
- **One IP is not one person behind NAT/CGNAT.** An office, a hotel, a
  co-working space or a mobile-carrier gateway shares every IP-keyed bucket. This
  is why the login tiers count failures only, why each endpoint has its own daily
  cap instead of drawing on one shared pool, and why every rate is env-tunable
  without a deploy.
- **`password_reset_required` is *not* an enumeration oracle — but the class
  docstring above it says it is.** `MyTokenObtainPairView`'s docstring carries an
  "Enumeration note, accepted deliberately" claiming the code is only ever
  returned for an address that has an account. **That is stale**: it describes the
  behaviour before the email-keyed counter existed. `_reset_required` is
  byte-identical whether or not the address has an account (only the *log* line
  distinguishes them, via `has_account`), and `email_is_locked()` fires for any
  submitted address. The admin door behaves the same way via `_refuse_locked`.
  Trust the code; the docstring needs deleting.

---

## 11. Quick reference — which control refuses what

| Symptom | Control | Where to look |
|---|---|---|
| 429 `rate_limited` on `/auth/token/` | one of four login tiers | `login_tier_exhausted` log line names it |
| 429 `rate_limited` on `/inquiries/` | burst or flood tier | `rate_limited` log line + `retry_after` magnitude |
| 429 `throttled_global` | the shared `anon` ceiling | should be unreachable today — if it fires, something new shipped without limits |
| 401 `password_reset_required` | the **account lock**, not a rate limit | `manage.py release_login_lock <email>` |
| 403 on `/admin/login/` with a message | the admin lock | same command |
| 400 `validation_error` on `/inquiries/` with no field errors | reCAPTCHA rejected it | `recaptcha_*` log events |
| 201 on `/inquiries/` but no lead row | the **dedupe window**, working | `inquiry_dedupe_hit` |
| every client sharing one bucket | Layer 1 lost the client IP | `client_ip_unresolved` — fix the edge |
