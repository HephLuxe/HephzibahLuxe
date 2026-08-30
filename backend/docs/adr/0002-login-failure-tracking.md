# ADR-0002 — Count failed logins on the account, not every login on the IP

**Status:** Accepted, implemented 2026-08-29 (§2 amended during implementation — see *Correction*)
**Date:** 2026-08-29
**Related:** `config/settings.py` (`RATE_LIMITS`), `apps/accounts/urls.py`, `apps/accounts/models.PasswordResetToken`, `docs/RATE_LIMITING_GUIDE.md` §6.1–6.2

---

## Context

Login carries three rate-limit tiers, all in `RATE_LIMITS`:

| tier | rate | key |
|---|---|---|
| `auth_login` | 10/m | IP |
| `auth_login_account` | 10/h | submitted email |
| `auth_login_daily` | 100/d | IP |

Two problems survive that arrangement, and they are the same problem seen from
two ends.

### P1 — every POST is counted, including the successful ones

The limiters are `@ratelimit(..., method="POST")` wrappers applied at the URL,
so they increment *before* the view knows whether the credentials were right. A
staff member logging in correctly spends anti-brute-force budget.

Behind NAT, one IP is not one person. Twelve staff logging in inside one minute
used to break at the sixth; raising `auth_login` to 10/m on 2026-08-29 moved that
to the eleventh, which is mitigation, not a fix.

### P2 — `auth_login_account` is the one tier with no daily backstop

Every other tier has a `_daily` sibling. This one does not, so a single email is
exposed to **10/h × 24 = 240 password guesses a day**, from an unlimited number
of source addresses, indefinitely and silently. A distributed attacker sends one
attempt per IP, so neither per-IP tier ever fires.

The obvious fix — add `auth_login_account_daily` — **must not be done**, and the
reason is the crux of this ADR:

> An account-keyed hard limit is a denial-of-service vector against your own
> staff. Anyone who knows `tola@hephzibahluxe.com` sends 50 wrong passwords from
> 50 addresses. No per-IP tier fires. The account tier is full. **Tola cannot log
> in until the window rolls**, and there is nothing Tola can do about it.

Trading a slow guessing exposure for a trivially-triggered lockout is a bad
trade. The tier almost certainly shipped without a daily sibling for this reason.

### What the codebase already knows about this

`PasswordResetToken` solves the identical shape correctly:

- `MAX_VERIFY_ATTEMPTS = 5`, counted **on the row, not on an IP**, so five wrong
  guesses burn the code no matter how many addresses they come from;
- checked *before* `check_password`, so a burned token costs no KDF work;
- recorded via `attempts_exhausted()` rather than `is_used = True`, deliberately,
  so the row stays visible to the lookup and the user is told to request a new
  code instead of receiving a generic "invalid or expired".

That is the pattern to copy. What login needs and the reset flow already has is
**a counter on the subject and an escape hatch for the real user.**

## Decision

Two changes that must land together — either alone makes things worse.

### 1. Count only failed logins

Replace the `auth_login` / `auth_login_daily` decorators on the login route with
an explicit check-then-increment inside the view:

- on entry, `django_ratelimit.core.is_ratelimited(..., increment=False)` — refuse
  if the bucket is already full;
- authenticate;
- increment **only** when authentication failed.

A correct login then costs nothing, which removes P1 outright: an office of any
size can log in, and the budget is spent only by people getting it wrong.

The per-endpoint decorators cannot express this — they run before the view and
have no access to the outcome — so this is a view-level change, not a config one.

### 2. A failure counter on the account, reset on success

Fields on the user (or a small side model keyed to it):

- `failed_login_count` — incremented on each failed authentication;
- `failed_login_at` — timestamp of the last failure, so the count can age out;
- threshold constant beside them, mirroring `MAX_VERIFY_ATTEMPTS`.

**Reset to zero on any successful authentication**, so ordinary fumbling never
accumulates toward the ceiling — only an unbroken run of failures escalates.

Above the threshold, refuse **before checking the password**, and point the user
at the reset flow: it already exists, is already rate-limited, and already
delivers a code out of band. That is the door, exactly as `attempts_exhausted()`
is for reset codes.

### Correction (found while implementing)

The first draft of this ADR claimed reset-on-success "defuses the DoS, because
the real user's correct password clears it". **That is wrong**, and building it
made the error obvious: if a correct password can still get through above the
ceiling, then the ceiling bounds nothing — an attacker keeps guessing and every
guess is still verified, at ~68 ms of PBKDF2 each. A per-account bound only
exists if it refuses *before* authenticating.

So the honest statement of the trade is:

- reset-on-success keeps **normal use** away from the ceiling;
- reaching the ceiling **does** refuse the account holder, correct password
  included;
- the recovery path is the password reset, whose code reaches an inbox the
  attacker cannot read.

The residual exposure is therefore *"an attacker can force the account holder to
complete a password reset"*, not *"an attacker can lock them out"*. That is the
standard trade for having any per-account bound at all, and it is the one being
accepted here. There is no design that both bounds per-account guessing and
guarantees the real user is never inconvenienced.

### Ordering constraint this creates

`MAX_FAILED_LOGINS` **must stay below both account-keyed rate tiers**
(`auth_login_account` 10/h, `auth_login_account_daily` 50/d). Those refuse with a
429 before the view can look at the account at all, so a higher ceiling would
make a locked account report a rate limit and the "reset your password"
instruction would never be shown — the recovery path would be invisible. Set to
**5**, matching `PasswordResetToken.MAX_VERIFY_ATTEMPTS`, and pinned by
`LoginAccountDailyBackstopTests.test_the_account_lock_binds_before_either_account_tier`.

### 3. Then, and only then, `auth_login_account_daily`

Once the counter above exists, a per-account daily cap becomes safe to add,
because it counts failures only and a genuine login clears it. Without §1 and §2
it is a lockout weapon; with them it is a backstop.

## Consequences

**Better:** correct logins stop consuming security budget, so the NAT problem
disappears rather than being tuned around. A per-account guessing bound finally
exists that is not defeated by rotating IPs. Attribution improves for free — a
failure count on the account is exactly what staff want when triaging "the client
says they can't log in", the same way `attempt_count` is on the reset admin.

**Worse:** the login view stops being a bare `TokenObtainPairView` subclass.
`MyTokenObtainPairView` was four lines; it now owns its own limiting.

It also has to override `handle_exception` to **re-raise `Ratelimited`**.
`Ratelimited` subclasses Django's `PermissionDenied`, which DRF maps to **403** —
so raising it from inside a view produces the wrong status. Every other limited
endpoint sidesteps this by raising *outside* DRF, from the URL decorator, which
is exactly what `apps/accounts/urls.py::_rl` warns about. Re-raising lets it
reach `RatelimitMiddleware`, so login's 429 comes from the same renderer as
everyone else's rather than a hand-rolled copy.

**Enumeration: raised, then closed.** A lock driven by `User.failed_login_count`
alone can only ever exist for an address that *has* an account, so
`password_reset_required` would itself have answered "is there an account here?"
for the price of five wrong passwords.

Closed by a **second counter, keyed on the submitted email, in the cache**
(`login_guard.record_email_failure`). It is incremented on every failed attempt
whether or not anything is behind the address, so a real account and an invented
one reach the identical response at the identical threshold. Pinned by
`LoginDoesNotLeakAccountExistenceTests.test_a_real_and_an_invented_address_are_indistinguishable`,
which asserts equal status *and* equal body.

The two counters are not redundant:

| | `User.failed_login_count` | `login_guard` email counter |
|---|---|---|
| storage | database | cache |
| exists for | real accounts only | any submitted address |
| durable across cache eviction | yes | no |
| visible to staff triaging "I can't sign in" | yes (admin) | no |

The view treats **either** at its ceiling as locked, so an eviction can only lose
the weaker of the two. Cache rather than a table for the email one deliberately:
a row per address anyone ever typed at the login form is a write amplifier and a
junk table, and the data is worthless once the window passes. Keys are SHA-256
hashed — cache keys surface in Redis tooling and monitoring, and an inventory of
every address ever tried is not something to leave there.

The **log line still distinguishes** the two cases (`has_account`, `user_id`),
because "which real account is under attack" is the question an operator has and
the log is not a surface an attacker reads.

**Watch:** the failure counter is a write on the auth path, so a burst of failed
logins becomes a burst of writes. Ageing via `failed_login_at` rather than a
scheduled reset keeps it to one row per attempt with no sweep. Given Neon
scale-to-zero (`RUNBOOK.md`), do not add a periodic job for this.

**Non-goal:** lockout as a punishment. Every account here is created by staff and
can be switched off with `set_user_status`; rate limiting is not the control for
a misbehaving human. This is about an attacker who does not have the password —
see `apps/core/throttling.UserBurstRateThrottle` for the same reasoning applied
to the authenticated surface.

## Operating it

Everything that can refuse a *sign-in* is reachable from `apps/accounts/admin.py`,
grouped so the two models read as one surface (the account and the reset codes it
recovers with).

| | where |
|---|---|
| Is this account locked? | `Login` column on the user changelist |
| Show me all locked accounts | `login lock` filter (evaluated in SQL, same rule as `login_locked()`) |
| What exactly is blocking them? | `Rate-limit buckets` on the detail page — the failure counter *and* both account-keyed tiers, live |
| Unblock them | **Release login lock** action |

**Release login lock clears all three things at once** — the durable counter on
the row, the cache counter that mirrors it, and both account-keyed rate buckets —
because an operator cannot see from the outside which one is refusing, and
clearing only the database counter looks like it worked and then hands the user a
429 on their next attempt. `login_guard.release_account()` owns that, so the
admin and any future caller cannot drift.

The counters are **read-only in the admin**. Typing a count by hand would let the
row disagree with the cache-side counter that shares the lock decision; the
action is the supported way to change it.

**Not in the admin, deliberately:** the IP-keyed tiers (`auth_login`,
`auth_login_daily`). They describe a machine, not an account, so there is no user
row to hang them on — and releasing them from one would silently unblock everyone
else behind that address. They also expire on their own (a minute, or a day).

## Rejected alternatives

**`auth_login_account_daily` on its own.** The lockout DoS above. Rejected.

**A generous cap (200/d) as a placeholder.** Against the current 240/day it moves
almost nothing while still carrying the DoS. Theatre.

**Exponential backoff per account.** Same lockout vector, harder to reason about,
and no in-house precedent — `PasswordResetToken` sets the house pattern and it is
a counter with a threshold.

**CAPTCHA on repeated failure.** Real option, and reCAPTCHA is already wired for
`apps/inquiries`. Rejected for now only because it is a frontend contract change
on the login form; worth revisiting if §2 proves insufficient.
