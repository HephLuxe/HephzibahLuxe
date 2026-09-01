# ADR-0004 — A `developer` role anchored in the environment, not the database

**Status:** Accepted, implemented 2026-09-01
**Date:** 2026-09-01
**Related:** `apps/accounts/developers.py`, `apps/accounts/signals.py`, `apps/accounts/admin.py`, `apps/accounts/models.User`, `config/settings.PLATFORM_DEVELOPER_EMAILS`, `apps/accounts/management/commands/ensure_developer.py`

---

## Context

The platform had three roles — `client`, `staff`, `admin` — and `User.save()`
derived Django's two permission flags from them:

```python
self.is_staff     = self.role in (UserRole.STAFF, UserRole.ADMIN)
self.is_superuser = self.role == UserRole.ADMIN
```

`is_superuser` in Django is not a label. `ModelBackend.has_perm` returns `True`
unconditionally for a superuser, so the Django admin grants an `admin` account
every permission on every model, including `accounts.User`. That is the intended
amount of power for the people running Hephzibah Luxe.

It stopped being the intended amount of power the moment an `admin` account was
issued to a **frontend contractor** so they could exercise the API. From that
login, six independent paths existed to take the platform's developer off their
own platform:

| # | Path | Effect |
|---|---|---|
| 1 | Change `role` to `client` in the admin | Loses `is_staff`/`is_superuser`; locked out of `/admin/` and every staff endpoint |
| 2 | Change `email` (it is `USERNAME_FIELD`) | Login identity and all future password-reset codes redirect to an inbox of the attacker's choosing |
| 3 | Admin "Change password" form | Silent, complete account takeover — strictly worse than deletion |
| 4 | Untick `is_active`, or run "Deactivate (offboard) selected users" | SimpleJWT rejects every request; refresh tokens blacklisted |
| 5 | `PATCH /users/<email>/status/` with `{"is_active": false}` | Same, over the API |
| 6 | Delete the row | Gone, with `SET_NULL` blanking the attribution behind it |

The only guard in place was `services.deactivate_user`'s "you cannot deactivate
your own account", which protects against a misclick and against nothing else.

Recovery from any of these required a shell on the production web service. Per
ADR-0002's notes on `release_login_lock`, Render's shell is a **paid-tier
feature** and the web service is on the free plan — so for the deployment as it
actually stands, recovery from path 1, 3, 4 or 6 was *not available at all*.

### What was needed

A fourth role, above `admin`, that:

* holds every privilege an admin holds (and then some),
* cannot be demoted, renamed, re-passworded, deactivated or deleted by an admin,
  a staff member or a client,
* survives a mistake as well as malice, and
* does not require the fifteen other apps to learn about a new role.

## Decision

### 1. `UserRole.DEVELOPER`, granting `is_staff` and `is_superuser`

```python
STAFF_ROLES     = frozenset({UserRole.STAFF, UserRole.ADMIN, UserRole.DEVELOPER})
SUPERUSER_ROLES = frozenset({UserRole.ADMIN, UserRole.DEVELOPER})
```

This is the whole of the "highest privileges" half, and it is deliberately
boring. Every authorisation site in the project — roughly forty of them across
`events`, `meetings`, `documents`, `portal`, `conversations`, `reminders`,
`contacts`, `budgets`, `document_hub`, `notifications` — routes through
`is_staff` / `is_superuser` or `core.permissions.is_staff_or_superuser`. Setting
both flags means a developer is admitted everywhere with **zero changes outside
`apps/accounts` and `apps/core`**.

The alternative — teaching each app about `UserRole.DEVELOPER` — was rejected
outright. A privilege that has to be recognised in forty places is a privilege
that will be missed in one of them, and the failure mode is a developer refused
by one endpoint for no discoverable reason.

### 2. The environment is the authority; the `role` column is a mirror

`settings.PLATFORM_DEVELOPER_EMAILS` (comma-separated, lowercased and validated
at boot) is the *only* thing consulted to decide whether an account is a
developer:

```python
def is_developer_email(email) -> bool:
    return email.strip().lower() in developer_emails()
```

Nothing in the authorisation path reads `User.role` to establish
developer-ness — `User.is_developer` is a property over the setting, not over
the column.

**This is the load-bearing decision.** Everything an admin can reach is inside
the database, because `is_superuser` is a licence to write any row. Putting the
answer in deployment config moves it behind a different credential: the Render
dashboard or the server's `.env`, which a contractor's platform login does not
touch. The consequence is stated as an invariant:

> An admin who rewrites the `role` column — through a surface we missed, a raw
> SQL session, a restored backup — has changed a label and nothing else.

The column still exists and still says `developer`, so the admin changelist and
`GET /users/?role=developer` have something to show. It is maintained by
`User.save()` and `manage.py ensure_developer`, and when it disagrees with the
environment it loses.

### 3. Self-repair, at every save and before every authentication

`developers.apply_state` re-derives the four fields that follow from
developer-ness — `role`, `is_active`, `is_staff`, `is_superuser` — and
`User.save()` applies it on the way past. No ORM write can leave a developer
demoted or deactivated, including one that goes through a code path nobody
guarded.

Two subtleties, both of which had to be handled or the guarantee is fiction:

* **`update_fields` is an allow-list.** A caller doing
  `save(update_fields=["is_active"])` to deactivate would have the correction
  silently dropped before it reached SQL. `save()` unions the corrected fields
  into `update_fields`.
* **`queryset.update()` bypasses `save()` entirely.** Nothing on the model can
  see it, and Django's `ModelBackend` and SimpleJWT both read `is_active`
  straight off the row *before* any project code runs — so a developer
  deactivated this way would genuinely be locked out. Both login paths therefore
  call `developers.repair_by_email(email)` **before authenticating**:
  `MyTokenObtainPairView.post` and `core.admin_login.guarded_admin_login`. For
  any address not in the list this is one frozenset membership test and no
  query, so ordinary logins — and every attacker's attempt — pay nothing.

`is_active = True` is unconditional in the derived state, which means **a
developer account cannot be deactivated by anyone — not an admin, not a second
developer, not itself.** `services.deactivate_user` refuses on the *target*
alone, without looking at the actor, precisely so the guard cannot disagree with
the derived state: a permitted call would write the deactivation, have it
corrected on the way to the database, and still return `changed: True`. Deletion
behaves the same way, for the same reason. That is intended. An offboarding switch the protected person can trip is not protection;
retiring a developer means removing the address from the environment first, after
which it is an ordinary admin account and every normal control applies again.

### 4. Every mutation surface refuses, visibly

`developers.enforce_can_manage(actor, target)` raises `PermissionDenied` when a
non-developer acts on a developer. It is called from `accounts.services`
(covering both the API endpoint and the admin action, which share it), the
registration serializer, and the admin. In the admin specifically:

* `has_change_permission` / `has_delete_permission` return `False` for the row.
  Django renders it **read-only rather than hidden**, because superusers keep
  the separate *view* permission.
* `user_change_password` refuses and redirects with a message — the read-only
  form alone leaves Django's separate change-password URL reachable, closing
  path 3.
* `delete_queryset` and each bulk action filter protected rows out and report
  how many were skipped. `delete_queryset` matters on its own: Django checks
  `has_delete_permission` once with `obj=None` for the bulk action, never
  per-row.
* the `role` dropdown drops "Developer" for non-developers, and
  `AdminUserCreationSerializer` refuses both `role=developer` and *any*
  registration at a configured developer address — closing the escalation
  direction.

One further surface, easy to miss and worth naming: `PasswordResetTokenAdmin`.
A developer is subject to the login lock like anyone else (see *Consequences*),
and the password reset is how they recover from it. An admin who could invalidate
or delete each reset code as it was issued would hold them out for the full
24-hour lock window — the closest thing to a real lockout left in the design, and
two clicks away. `invalidate_tokens`, `delete_queryset` and
`has_delete_permission` therefore skip tokens belonging to a protected account,
surgically, so an admin batching other rows is not refused wholesale.

**Visible-and-locked, not hidden**, was chosen over filtering the row out of the
changelist and `GET /users/`. Hiding is weaker than it looks — the account still
appears in `created_by` attribution stamps and in counts — and it converts a
clear refusal into inexplicable behaviour. An admin who cannot see why something
fails works around it; an admin who reads "protected developer account" does not.

### 5. `manage.py ensure_developer`, run on deploy

Idempotent: creates the accounts named in the environment if missing, repairs
their four derived fields if drifted, and **never touches an existing password**
(a version that re-randomised on each deploy would lock the developer out on
every push). Running it in the release phase is what makes the account survive a
database restore, a fresh environment, or a delete that somehow got past the
`pre_delete` guard.

Deliberately not a data migration: a migration runs once against a frozen
snapshot of the environment, cannot react to the list changing, and would commit
an email address to version control — the one place this design keeps it out of.

### 6. `pre_delete` as the last resort

`Model.delete()` passes through neither `save()` nor the admin's permission
hooks. `pre_delete` is the single hook that `QuerySet.delete()`, `Model.delete()`
and cascades all fire, so the guard lives there and raises `ProtectedAccountError`.

It raises a plain `Exception`, not `PermissionDenied`, on purpose: DRF would
render that as a tidy 403 and the admin would catch it, both of which invite a
caller to treat this as routine. Reaching this guard means three earlier layers
were bypassed, so it should surface as a 500 and an alert. It aborts the entire
transaction, which is also intended — a bulk delete containing a developer
should change nothing rather than delete the others and skip one.

### 7. Multiple developers are peers, not a hierarchy

`PLATFORM_DEVELOPER_EMAILS` takes a list, and adding a second address is an env
change plus a redeploy — no migration, no code edit, no admin click. An account
that already exists is **promoted in place**: same row, same password, no
`force_password_change`, nothing re-created. One that does not exist is created
by `ensure_developer` with a random password recovered through the reset flow.

Developers can manage each other (`can_manage` returns True between them), with
the two carve-outs above that apply to everyone. There is deliberately no
"first" or "owner" developer: ranking them would need a tie-break stored
somewhere, and the only place safe enough to store it is the environment, which
is already the list. If a developer needs removing, the answer is the same as
everywhere else in this ADR — take them out of the environment, redeploy, and
the account becomes an ordinary one that every normal control applies to.

## Consequences

**The lockout scenario is closed.** Taking the developer off the platform now
requires the deployment environment, which is a different credential from any
platform login. Paths 1–6 above are each covered twice: by a surface guard that
produces a clear refusal, and by `save()`/`pre_delete`/`repair` underneath, which
covers paths not yet invented.

**Layering is what makes this maintainable.** If an admin action ships next year
without a guard, the worst it achieves is corrupting a mirror column that the
next sign-in puts back. The surface guards exist for the error message; the
derived state exists for the guarantee.

**A developer is still subject to the login lock** (ADR-0002) and to every rate
limit. Those are anti-guessing controls, not privileges, and exempting the
highest-value account from them would be exactly backwards. The admin's "Release
login lock" action is therefore *not* filtered for protected accounts — it only
ever restores access.

**Deliberately out of scope:** anyone with the deployment environment, a shell on
the server, or write access to both the database and the env vars. Those
credentials define who runs the platform and no in-app control can outrank them.
The threat modelled here is a hostile or careless **platform admin**, which is
the account type actually being handed out.

**Cost:** one environment variable that must be set on each deploy target, and
one release-phase command. If `PLATFORM_DEVELOPER_EMAILS` is unset the feature is
simply inert — no protected accounts — which is the correct behaviour for CI and
for a reviewer's checkout, and is why an empty list is not an error.

## Testing

`apps/accounts/test_developer_role.py`, 53 tests, each written as an attack from
the contractor's admin login. The load-bearing ones:

- a `queryset.update()` deactivation is repaired, and the developer **logs in
  successfully** through the real endpoint afterwards;
- `save(update_fields=["is_active"])` cannot smuggle a demotion past the union;
- a `role=developer` column written directly by SQL grants nothing;
- the admin's change, delete, bulk-delete, deactivate, force-password-change and
  change-password paths each refuse;
- `POST /users/register/` refuses both the role and the address;
- the admin add form refuses a protected address (the takeover that
  `has_change_permission` cannot cover, because on an add there is no object
  yet to refuse);
- narrowing the role dropdown for an admin does not leak into a developer's own
  form — the reason the restriction is applied to `self.fields` per instance
  rather than to class-level `base_fields`;
- an admin can neither invalidate nor delete a developer's password-reset
  tokens, while other rows in the same action still go through;
- an existing `admin` account at a configured address is promoted **in place** —
  same primary key, same password — which is the adoption path for a developer
  who already has an account;
- adding a second address promotes that account too, and removing an address
  returns it to being an ordinary manageable admin;
- one developer cannot deactivate another, but can otherwise manage them;
- `ensure_developer` is idempotent and does not reset an existing password;
- `repair()` never promotes a non-developer, so it cannot become an escalation.
