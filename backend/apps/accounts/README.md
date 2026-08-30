# Accounts App

This application manages user authentication, registration, password resets, and session tokens. It is built to prioritize security, using a custom User model and JWT for authentication.

## Key Features & Flows

### 1. Authentication & JWT
- Uses `rest_framework_simplejwt` for JWT generation.
- **Custom Token Serializer**: The `CustomTokenObtainPairSerializer` injects extra user data (like `first_name`, `last_name`, and the `force_password_change` flag) directly into the login token response payload.

### 1b. Login rate limiting and the account lock

See [`docs/adr/0002-login-failure-tracking.md`](../../docs/adr/0002-login-failure-tracking.md).

**Login is the one limited endpoint not wrapped at the URL.** Every other one uses
`@ratelimit(...)` on the URLconf; login cannot, because that decorator increments
*before* the view knows whether the credentials were right — so a correct sign-in
spent anti-brute-force budget, and behind NAT one IP is not one person. A dozen
staff arriving together could exhaust a per-IP burst while doing nothing wrong.

`login_guard.py` splits it: `get_usage(increment=False)` checks on the way in,
`increment=True` counts on the way out, and **only when authentication actually
failed**. Same groups, same rates, same bucket strings as the decorator produced.

Four tiers, two axes:

| tier | rate | keyed on |
|---|---|---|
| `auth_login` | 10/m | IP |
| `auth_login_account` | 10/h | submitted email |
| `auth_login_account_daily` | 50/d | submitted email |
| `auth_login_daily` | 100/d | IP |

**The account lock.** `User.failed_login_count` counts consecutive failures;
`MAX_FAILED_LOGINS` is 5 (matching `PasswordResetToken.MAX_VERIFY_ATTEMPTS` — same
problem, same answer) and an untouched run ages out after `FAILED_LOGIN_WINDOW`
(24h), lazily, with no sweep job. Any successful sign-in resets it to zero, so
only an unbroken run escalates.

At the ceiling the account is refused **before its password is checked**. It has
to be that way round — verifying first would leave guessing unbounded and spend
~68 ms of PBKDF2 per guess — so a correct password cannot rescue a locked
account. The recovery path is the password reset, whose code reaches an inbox an
attacker cannot read; completing it clears the lock. The residual exposure is
"an attacker can force a password reset", not "an attacker can lock you out",
which is the standard trade for having any per-account bound at all.

**Two counters, not one.** A lock driven by `failed_login_count` alone could only
exist for an address that *has* an account, so `password_reset_required` would
itself have answered "is there an account here?" for five wrong passwords. A
second counter keyed on the submitted **email** lives in the cache and is
incremented whether or not anything is behind the address, making a real account
and an invented one indistinguishable. The database one is durable and visible in
the admin; the cache one covers what the other cannot represent. Either at its
ceiling means locked, so an eviction can only lose the weaker of the two.

**Releasing a lock (admin).** `CustomUserAdmin` carries a `Login` status column, a
`login lock` filter, a live `Rate-limit buckets` readout, and the **Release login
lock** action.

> Three separate things can refuse one address and an operator cannot see which
> from the outside. **Clearing only the database counter would look like it worked
> and then hand the user a 429 on their next attempt.**
> `login_guard.release_account()` owns the whole release — the durable counter,
> the cache counter, and both account-keyed rate buckets — so the admin can't
> drift from it.

The counters are read-only in the admin; typing one by hand would let the row
disagree with the cache-side counter that shares the lock decision. The IP-keyed
tiers are deliberately absent: they describe a machine, not an account, and
releasing them from one user would unblock everyone behind that address.

### 1c. The Django admin's login form

`apps/core/admin_login.py`, routed from `config/urls.py`.

**The hole.** Everything in 1b protected `POST /api/v1/auth/token/` and nothing
else. `/admin/login/` is neither a DRF view (so `DEFAULT_THROTTLE_CLASSES` never
ran) nor wrapped by the decorators in `urls.py`, so it had **no limit and no lock
of any kind**: unlimited password guesses against any `is_staff` account, and an
account locked out of the API could still sign in through the admin — which made
the API's five-strike lock decorative for exactly the accounts worth attacking.
`role=admin` sets `is_superuser=True` in `User.save()`, so that door is the whole
operational control plane: users, notification toggles, feature settings,
reference counters, password-reset tokens.

**A URL override, not an `AdminSite` subclass.** `config/urls.py` lists
`admin/login/` *before* `admin/`. Django resolves in order, so `guarded_admin_login`
wins and `admin.site.urls` never sees the request. No swapping
`django.contrib.admin` for a custom `AdminConfig`, and `reverse('admin:login')`
still produces `/admin/login/` — which now lands on the guard, so every "you must
log in first" redirect in the admin is covered without touching a registration.

| tier | rate | keyed on | shared with the API? |
|---|---|---|---|
| `admin_login` | 5/m | IP | no — its own group |
| `auth_login_account` | 10/h | submitted email | **yes** |
| `auth_login_account_daily` | 50/d | submitted email | **yes** |
| `admin_login_daily` | 50/d | IP | no — its own group |

Separate IP groups so operator traffic and public API traffic never draw down
each other's buckets; shared account tiers because one account under attack is
one account whichever door is being tried. Tighter per-minute than the API's
10/m: the admin has a handful of human users and no programmatic callers, so
nothing legitimate here retries. Only POSTs are measured — counting a GET would
let a page refresh spend an operator's allowance. The 429 is raised as
`Ratelimited` and rendered by `RatelimitMiddleware` into the same envelope and
real `Retry-After` the API produces.

**Superusers are not exempt.** They are the highest-value target, so exempting
them would defeat the control. What makes that safe rather than reckless is that
two admin-independent recovery paths exist and are tested — see below.

**The refusal REDIRECTS; it does not re-render.** This is the load-bearing line
and it looks like a stylistic choice, so:

```python
messages.error(request, LOCKED_MESSAGE)
return HttpResponseRedirect(request.get_full_path())   # NOT admin.site.login(...)
```

`admin.site.login` is a *view*. Handed the POST that is being refused, it
authenticates it — so re-rendering the form through it signs the locked account
straight in, and anyone holding the correct password walks past the lock
untouched. The refusal path would perform the login it exists to prevent. A 302
throws the submitted credentials away; the browser's follow-up is a plain GET
with nothing left to authenticate. `get_full_path()` rather than a hardcoded
path so an `?next=` survives the bounce. Both properties are pinned by
`AdminLoginIsGuardedTests` — `test_the_account_lock_applies_here_too` posts the
**correct** password against a locked account and asserts no session was created.

The lock is checked *before* the password, exactly as the API does; verifying
first would leave guessing unbounded and spend ~68 ms of PBKDF2 per attempt. A
locked address and an address with no account produce the identical response, so
the page cannot be used to enumerate admins — that is what the email-keyed cache
counter in 1b is for.

**One person's flood cannot lock another person out.** The two axes behave
differently when a guesser shares an operator's IP, and the difference is worth
being explicit about:

| | keyed on | so a guesser on your connection… |
|---|---|---|
| the **lock** (5 strikes) | the **email typed** | cannot touch you — hammering `nobody@example.com` locks that address, not yours |
| the **IP tiers** (5/m, 50/d) | the **connection** | *can* spend your budget; everyone behind that address gets a 429 until the window rolls |

That asymmetry is deliberate. An account-keyed lock that any passer-by could
trigger would be a denial-of-service tool pointed at your own operators, so the
durable lock follows the address being attacked and never the machine attacking
it. The IP tiers are the reverse — they describe a machine, which is why
`release_account()` refuses to clear them and why the fix for a shared-IP 429 is
to change networks, not to clear a counter. Both halves are pinned by
`test_someone_elses_flood_from_your_ip_does_not_lock_your_account` and
`test_but_their_flood_does_spend_the_shared_ip_budget`. The admin door's IP
tiers are also its own groups, so a flood here cannot stop clients signing in to
the portal from the same address.

**Recovery, which is the part that matters.** The admin's own *Release login
lock* action is inside the admin, so a lock applied here would otherwise be the
release button behind the locked door. Four ways out, in the order you would
normally reach for them:

1. **A password reset** — the self-service path. Its code goes to an inbox an
   attacker cannot read, and completing the flow clears the lock.
2. **`python manage.py release_login_lock <email>`** from a shell *on the
   deployment* (`--all` for the both-admins-locked case). It calls the same
   `login_guard.release_account()` the admin action does, so the two cannot
   drift. Two of the three counters are in Redis at a private-network address,
   so running it off-platform clears the database counter against a local cache
   and leaves the lock in place. Note `manage.py changepassword` does **not**
   rescue you either — the lock is `failed_login_count`, which is independent of
   the password.
3. **Wait 24 hours** — `FAILED_LOGIN_WINDOW` ages both counters out lazily, with
   no sweep job.
4. **Another admin presses the button** — easiest of all, when one exists. 2 and
   3 are what cover the case where nobody can get in.

Paths 1–3 are all admin-independent, which is what makes "superusers are not
exempt" a safe position rather than a reckless one.

### 2. Admin-Driven Registration
- **Flow**: Only staff/superusers can register new client accounts (via `register_user` view).
- **Temporary Password**: The `AdminUserCreationSerializer` automatically generates a cryptographically secure temporary password.
- **Async Delivery**: The credentials (with a secure login link) are sent via `utils.send_user_credentials_email(user, temporary_password)`, which calls `notifications.services.queue_notification()` — same pipeline as every other email in the platform (a durable `Notification` row, dispatched to the in-process thread pool on commit, re-driven by the `notification_retry` cron sweep if the send is lost, Brevo template `user_credentials`, admin audit trail, toggleable via `NotificationTypeSettings`), ensuring the API response isn't blocked. The temporary password lands in `Notification.context` and is **redacted once the row reaches a terminal state** — see `apps/notifications/README.md`.

### 3. Forced Password Change
- When a new account is created, the `force_password_change` flag is set to `True`. 
- Upon first login, clients are required to set a permanent, secure password before they are granted full access to the platform.

### 4. Password Reset (3-Phase Flow)
Password resets use a 6-digit code, **stored hashed**, valid for 30 minutes, with
a five-guess budget per code:
1. **Request (`/api/password-reset/request/`)**: Validates the email. To prevent user enumeration, it *always* returns a 200 OK regardless of whether the email exists. Generates a `PasswordResetToken` and sends the 6-digit code via `utils.send_password_reset_email(user, code)` — `notifications.services.queue_notification()`, Brevo template `password_reset`.
2. **Verify (`/api/password-reset/verify/`)**: Pre-checks the 6-digit code to ensure it matches the email and hasn't expired, returning a success message if valid.
3. **Confirm (`/api/password-reset/confirm/`)**: Accepts the code and the new password. It changes the password and immediately marks the token as used (`is_used=True`) to prevent replay attacks.

#### How the code is stored, and why it matters more than it looks

A six-digit code is a 10^6 search space, which makes both halves of this
non-optional:

**Hashed with the password hasher, not stored and not merely digested.** These
rows used to hold the live code in the clear, next to the user it belonged to and
the IP that requested it, and nothing deleted them — so database access, a
backup, or the admin changelist (which printed it in a column and let you search
by it) was a second way to read a credential. A bare SHA-256 would not have
helped: 10^6 digests is a sub-second table. `make_password` gives per-row salt and
PBKDF2's iteration count instead. Measured cost: **~69 ms** on `make_password`
(once per reset *request*) and ~69 ms on `check_password` (once per *verify*) —
irrelevant against the `10/m` limit on that endpoint, and the entire point.

Because the hash is salted it cannot be looked up by value, so
`utils.verify_reset_code` fetches the user's outstanding token and checks the code
against it. That works because `create_password_reset_token` invalidates prior
unused tokens, leaving at most one — an invariant the tests pin.

`create_password_reset_token` returns `(token, code)`. The plaintext exists only
in that return value; the caller mails it immediately or it is gone. There is no
way to recover a code afterwards, for us or for anyone reading the table.

**A five-guess budget (`MAX_VERIFY_ATTEMPTS`), which is what makes the 30-minute
TTL safe.** The TTL was raised from 15 minutes because a failed send is now
re-driven only by the `notification_retry` cron sweep, so a transient Brevo blip
could otherwise deliver a code that was already dead. But a longer window is also
a longer guessing window, previously bounded only by the per-IP verify limits.
Five guesses per issued code against 10^6 possibilities closes that.

An exhausted token is marked by `attempt_count`, **not** by `is_used` —
deliberately. Setting `is_used` would hide the row from the "this user's
outstanding token" lookup, so the next attempt would fall through to the generic
"invalid or expired code" and the user would never be told to request a new one,
which is the whole reason the lockout message exists. `is_valid()` folds
`attempts_exhausted()` in, so nothing can treat a burned token as usable by
checking `is_used` alone.

Neither `PasswordResetTokenAdmin` nor `__str__` exposes the code or the hash;
`__str__` in particular ends up in the admin changelist, log lines and error
pages.

### 4a. What `PATCH /users/me/update/` may and may not change

`first_name`, `last_name`, `timezone`. **Not `email`.**

`email` is `USERNAME_FIELD`, so changing it changes the account's login identity
and redirects every future password-reset code and credentials email along with
it. Self-service it was unverified: no proof of owning the new address and no
notice to the old one, so a typo silently sent all of them somewhere the account
holder does not control — and the lockout surfaced at the next password reset,
which is exactly when recovery is already needed.

A quieter second effect: `notifications.views._recipient_q` matches on
`recipient_user` **or** `recipient_email__iexact`, and that fallback is
load-bearing — it is how a lead who later becomes a client still reads the
acknowledgement sent before their account existed. Rewriting your own email
therefore rewrites which notification rows you can read. `unique=True` blocks
taking a live account's address, but a deleted user's is free.

Refused with a **400**, not silently dropped. `read_only_fields` alone would
strip the field and return 200 with the old address echoed back — the caller
believes it worked and finds out at the next reset. Only a *different* address
is refused, so a PUT that echoes the current one back (or differs only in case)
is unaffected.

Changes go through staff in the Django admin. That costs a client nothing here,
because accounts are staff-provisioned in the first place (`register_user` is
staff-only), and it puts a human identity check in front of a login-identity
change. If self-service is wanted later, the shape is a verified change — a code
to the **new** address to prove ownership, plus a notice to the **old** one so
the original owner can react. That is a feature, not a widened serializer.

Pinned by `EmailIsNotSelfServiceTests`, which also holds the line on
`role`/`is_staff`/`is_superuser` — already absent from the serializer, but this
is the endpoint where a widened field list would do the most damage.

### 4b. `User.timezone` — which calendar day a client is on

An IANA name (`Africa/Lagos`, `America/New_York`), blank to inherit
`settings.PLATFORM_DEFAULT_TIMEZONE`. Writable by the account holder via
`PATCH /users/me/update/`, and by staff in the Django admin.

**Not for rendering.** Every timestamp the API returns is UTC ISO-8601 and the
frontend localises it; `TIME_ZONE` stays `UTC` and `USE_TZ` stays `True`.

It exists because some of this platform's most important fields are calendar
**days**, not instants: `PaymentMilestone.due_date` and `Meeting.date` are naive
`DateField`s meaning "this day, where the client lives". The daily digests compare
against them, and comparing a client's due date to *UTC's* today is off by one for
anyone far enough from UTC — a three-day payment lookahead that fires two or four
days out. On a platform used worldwide that is not a corner case. See
`apps/core/timezones.py` for the full reasoning and
`apps/document_hub/tasks.py` / `apps/meetings/tasks.py` for the query shape.

Deliberately a plain `CharField`, not `choices=`: the IANA database gains, renames
and merges zones, and pinning ~600 names into the model would make every tzdata
update a schema change. Validated on the way in instead — `User.clean()` for the
admin, `TimezoneField` for the API — while `timezones.resolve_timezone_name()`
deliberately degrades to UTC on *read*, so one bad row can never take out a whole
digest run.

## File Structure & Logic Separation

- **`models.py`**: Contains the custom User model (including `timezone`, and the `failed_login_count` / `failed_login_at` pair behind the account lock) and `PasswordResetToken`.
- **`serializers.py`**: Handles all input validation (passwords, 6-digit codes) and creation logic. 
- **`views.py`**: Thin coordinators. They receive the request, pass data to serializers, and return responses. They do *not* contain heavy business logic or blocking I/O. The exception is `MyTokenObtainPairView`, which owns its own rate limiting (see 1b) and overrides `handle_exception` to **re-raise `Ratelimited`** — it subclasses Django's `PermissionDenied`, which DRF maps to 403, so without that a limited login would answer with the wrong status instead of the project's standard 429.
- **`login_guard.py`**: The login tiers, the email-keyed failure counter, and `release_account()`. Exists because login counts failures only — see 1b.
- **`management/commands/release_login_lock.py`**: The break-glass (see 1c). The admin's *Release login lock* action is inside the door a lock closes, so this is the shell-side path back in. Calls the same `login_guard.release_account()`, never a hand-rolled ORM update — that would clear the database counter only and hand you a 429 on the next attempt.
- **`apps/core/admin_login.py`** (not in this app, but part of this story): the guard on `/admin/login/`. It lives in `core` because it is a URLconf-level override of a `django.contrib.admin` view rather than anything accounts owns, but it imports `login_guard` and shares the account tiers and the lock. See 1c.
- **`utils.py`**: Contains helper functions for generating random codes/passwords, creating reset tokens, checking code validity, and queueing the two account emails (credentials, password reset) via `notifications.services.queue_notification()`. `tasks.py` here holds only scheduled housekeeping (`flush_expired_jwt_tokens`, `prune_expired_reset_tokens`, both in the `daily_maintenance` cron group) — email sending goes through the shared `notifications` app's `send_notification_task` rather than an app-specific one. `RESET_CODE_TTL_MINUTES` (30) is the single source for both `PasswordResetToken.expires_at` and the `expires_in_minutes` merge field, so the number in the email can't drift from the number in the database.

### 5. User directory — `GET /users/` (staff only)

Backs the staff dashboard's client list. Query params are all optional and
combine:

| Param | Notes |
|---|---|
| `role=` | repeatable — `?role=staff&role=admin` |
| `is_active=` | `true` / `false` |
| `search=` | case-insensitive over email / first / last name |
| `ordering=` | `-` for descending; default `-date_joined` |

Two things worth knowing: each row carries **`portal_id`** (null for staff, and
for a client whose portal signal hasn't run) so the frontend can link straight
through to the portal without a second lookup; and `ordering` is **allow-listed**
(`date_joined`, `last_login`, `email`, `first_name`, `last_name`, `role`) — an
arbitrary value is rejected with 400 rather than letting a caller sort by
internals to probe the table. The queryset `select_related("portal")` so the
`portal_id` field doesn't fire a query per row.

### 6. Deactivation / offboarding (reversible)

Offboarding is a **reversible state, not a delete** — see `services.py`.

```http
PATCH /api/v1/users/<email>/status/          (staff only)
{ "is_active": false, "reason": "Contract completed" }   → offboard
{ "is_active": true }                                    → restore
```

One symmetric endpoint on purpose: reversing is the same call with `true`, so a
UI toggle maps straight onto it and there's no separate "undo" path to drift.
The Django admin exposes the same thing as the *Deactivate / Re-activate* bulk
actions — **both route through `services.deactivate_user` / `reactivate_user`**,
so they can't diverge. Don't flip `is_active` directly; you'd skip the audit
stamp and the token revocation.

**Why not delete the user:** they're the FK target of their portal, their events,
and every `created_by`/`last_updated_by` stamp in the project. Deactivating keeps
all of it and is undoable; deleting would blank the attribution (`SET_NULL`) and
cascade the portal away.

**It takes effect immediately.** SimpleJWT's `CHECK_USER_IS_ACTIVE` defaults to
`True` and isn't overridden, so an inactive user is rejected on *every*
authenticated request — even an unexpired access token stops working, no waiting
for expiry. `deactivate_user` additionally blacklists outstanding **refresh**
tokens (the `token_blacklist` app is installed), because a refresh can otherwise
be exchanged for a new access token without loading the user.

**The audit trio** — `deactivated_at`, `deactivated_by`, `deactivation_reason` —
is **cleared on reactivation**, so a populated `deactivated_at` always means
"off right now". Exposed read-only in `GET /users/` as `deactivated_at` /
`deactivated_by_display` / `deactivation_reason`.

Gotchas:
- **You cannot deactivate yourself** — enforced in the service, so the API and
  the admin action both honour it (an easy misclick, painful to undo).
- **Both operations are idempotent.** Re-deactivating an already-inactive user
  is a no-op that *preserves the original reason* rather than overwriting who
  did it first.
- Reactivation does **not** un-blacklist old refresh tokens — the user simply
  logs in again for a fresh pair. Nothing else needs restoring.
- Covered by `tests.py::DeactivationTests`, including a test that asserts the
  user's portal and events survive an offboarding.
