# Core App

The `core` app is the shared foundation the other apps build on — it owns no
database tables of its own and exposes no endpoints. Instead it provides the
abstract base models, the permission vocabulary, the standard error envelope,
the file-upload path builders, pagination strategies, the deep-link registry,
in-process background execution, and per-recipient calendar dates — concerns the
feature apps (`meetings`, `portal`, `document_hub`, …) reuse so they are defined
once, not re-implemented per app.

---

## Abstract Base Models (`models.py`)

All `abstract = True` — they add fields to the models that inherit them and
create no tables/migrations of their own.

* **`TimestampedModel`**: adds `created_at` (auto_now_add) and `updated_at`
  (auto_now).
* **`UUIDPrimaryKeyModel`**: replaces the default integer PK with a
  non-editable UUID. Used for anything exposed in a URL or returned to a client
  so primary keys are never guessable/enumerable.
* **`UUIDTimestampedModel`**: the common case — UUID PK **and** timestamps in
  one base (`reminders.Reminder`, `document_hub.*`, `notifications.Notification`
  build on this).

---

## Permissions (`permissions.py`)

Two layers, used deliberately together:

* **`IsStaffOrSuperuser`** (DRF `BasePermission`): request-level check for the
  `@permission_classes([...])` decorator. Auto-evaluated by DRF before the view
  body runs — no object needed.
* **Helper functions** for object-level checks inside function-based view
  bodies (DRF does not auto-run object permissions for FBVs):
  * `is_staff_or_superuser(user)` — Hephzibah Luxe team.
  * `is_event_celebrant(user, event)` / `is_portal_owner(user, portal)` — the
    owning client.
  * `can_access_event(user, event)` / `can_access_portal(user, portal)` /
    `can_access_portal_resource(user, obj)` — the common composite: staff sees
    anything, a client sees only their own.
* **`enforce(condition, message)`**: raises DRF `PermissionDenied` when the
  check fails instead of returning a bool — lets a view read as one guard line
  (`enforce(can_access_event(user, event))`) and hands off to the error handler
  below for the response shape.

---

## Error Envelope (`error_codes.py` + `exceptions.py`)

Every error response across the API shares one shape. **Frontend: branch on the
machine-readable `code`, never on the human `detail` string** (detail wording
can change; codes are stable). The shape:

```jsonc
// 4xx error
{
  "detail": "Only staff can modify meetings.",  // human-readable, for display/logging
  "code": "permission_denied",                   // machine-readable — switch on this
  "errors": {                                     // present only on validation errors
    "scheduled_at": ["This field is required."]
  }
}
```

* **`ratelimit.py`**: the single source of truth for **"who is the client"**.
  Four things need that answer and must never disagree — the per-endpoint
  rate-limit key callables, the DRF throttles, django-ratelimit's built-in
  `key='ip'` path (via `RATELIMIT_IP_META_KEY`), and the password-reset audit
  trail. Nothing else in the codebase may read `REMOTE_ADDR` or
  `X-Forwarded-For`; before this module owned all four there were three
  implementations and two were wrong.

  * **`resolve_client_ip(request)`** — the exact client address. **It walks
    `X-Forwarded-For` right-to-left and returns the first entry that isn't
    inside the trusted-proxy CIDR set (RFC-1918 + loopback).** Reading
    right-to-left is what makes the header safe to use at all: every proxy in
    both deployment shapes *appends*, so anything a client prepends sits to the
    left of the real address and is never reached — a caller sending
    `X-Forwarded-For: 9.9.9.9` is still bucketed under their own address. If
    `REMOTE_ADDR` is **not** a trusted proxy the connection came in directly, so
    XFF is fully attacker-controlled and is discarded outright.

    This is deliberately *not* DRF's `NUM_PROXIES` approach, which says "take
    the Nth entry from the right". A count goes stale silently the moment a hop
    is added or removed; trusting by *identity* survives that. `NUM_PROXIES` is
    unset and unreachable — every throttle overrides `get_ident` — and
    `test_no_throttle_in_the_project_consults_drf_num_proxies` keeps it that way.
  * **`bucket_ip(request)`** — the same address masked to a prefix (IPv4 /32,
    IPv6 /64) and used for **every** rate-limit bucket. The IPv6 default is the
    point: a client typically owns a whole /64, so an unmasked key would hand
    them a fresh bucket per request and make every IP-keyed limit free to bypass.
  * **`client_ip` / `ip_and_email` / `email_key_for`** — thin adapters matching
    django-ratelimit's `(group, request)` callable signature, so no consumer is
    ever tempted to write a fifth implementation to fit its own.

* **`error_codes.py`**: the canonical `code` values a client may receive —
  `authentication_required`, `invalid_credentials`, `permission_denied`,
  `not_found`, `validation_error`, `invalid_transition`, `contacts_locked`,
  `event_details_locked`, `confirmation_required`, `rate_limited`,
  `throttled_global`, `password_reset_required`, `token_invalid`,
  `internal_error`. New codes are added here as constants, never inlined as
  string literals in views. There is deliberately **no** `token_expired`:
  SimpleJWT raises `InvalidToken` for an expired token and a malformed one
  alike, so it could never be emitted, and a code a client can never receive is
  worse than none — it invites a branch that never runs.
  `password_reset_required` is a **401 on login** and is distinct from
  `invalid_credentials` because retrying cannot help: the account has run out of
  sign-in attempts and the holder has to complete a password reset (or wait the
  window out). See `apps/accounts/README.md` §1b.
  `rate_limited` and `throttled_global` are both 429s and are deliberately
  distinct: the first is a per-endpoint limit the caller tripped themselves
  (retry after the window and it clears), the second is the shared ceiling on
  all unauthenticated traffic from one IP, which may have been exhausted by
  somebody else behind the same NAT — so retrying sooner will not help. They
  used to share one code, which made a 429 impossible to attribute.
* **`custom_exception_handler`**: the project-wide DRF handler. It does two
  things:
  1. **Injects `code`** into responses DRF already built for handled
     exceptions (auth, permission, not-found, throttle, validation). This is
     what makes `enforce()` and `get_object_or_404` — scattered across every
     app — produce the envelope without touching each call site. It also
     rewraps the one shape DRF gets wrong: a bare `raise ValidationError("msg")`
     from a `services.py` function (a raw list body) is normalised into
     `{detail, code, errors}`.
  2. **Catches the unexpected**: a real bug bubbling out of a view is logged as
     one structured record, reported to Sentry (if configured), and returned as
     the standard 500 with `code=internal_error`.

> Views that build error responses *directly* (not via a raised exception)
> still use a local `_error()` helper — this handler only sees exceptions that
> actually propagate out of a view.

---

## File Upload Paths (`utils.py`)

* **`parse_decimal(value)`**: coerces a raw request-body value (str/int/float)
  into a `Decimal`, or `None` if it isn't a valid number. Money fields read
  straight off `request.data` (bypassing a serializer) would otherwise keep the
  client's JSON type and raise `TypeError` during arithmetic against a `Decimal`
  model property — every view that reads a money field directly routes it
  through this first.
* **Upload-path builders** (`event_cover_upload_path`, `event_image_upload_path`,
  `prep_upload_path`, `budget_receipt_upload_path`, `client_document_upload_path`,
  `invoice_upload_path`, `receipt_upload_path`, `contact_photo_upload_path`, …):
  each `FileField.upload_to` callable builds a per-portal, per-object storage
  path. Paths are keyed on the **immutable `pk`**, not the slug — the slug is a
  cosmetic suffix only, so a later rename never orphans an already-stored file.
  `_safe_portal_id()` walks the object → portal chain and degrades to
  `"unknown"` rather than raising if a link is missing.

---

## Pagination (`pagination.py`)

Two strategies, opt-in per view (never set as the global default, so existing
un-paginated lists keep their raw-array shape unless a view opts in):

* **`StandardCursorPagination`**: cursor-based, ordered `(-created_at, -id)`.
  Deterministic under concurrent inserts (offset pagination shifts rows
  mid-scroll); the two-field ordering gives a stable tie-breaker. Default for
  unbounded/streaming lists. `?page_size=` (max 100). **No total count** — you
  page by following `next`/`previous` cursors:

  ```jsonc
  { "next": "https://…?cursor=cD0y", "previous": null, "results": [ /* items */ ] }
  ```

* **`StandardPageNumberPagination`**: classic numbered pages **with a total
  count** (`page_size=7`, `?page=`, `?page_size=` up to 50). Used where the UI
  is numbered-page shaped and needs a total (e.g. the Budget Payment History
  table, "Showing 1 to 7 of 14"):

  ```jsonc
  { "count": 14, "next": "https://…?page=2", "previous": null, "results": [ /* items */ ] }
  ```

> **Frontend:** a paginated endpoint returns this envelope (`results` + paging),
> not a bare array. Whether an endpoint is paginated is per-view — check the
> endpoint's own docs.

---

## Deep Links (`deeplinks.py`)

Turns a *domain object* into a portal route, so a reminder or a conversation
pill can point at a real thing (a conversation, a prep item, an invoice) rather
than a hand-typed URL string.

* **`TARGET_TYPES`**: a registry mapping each public `target_type` slug (e.g.
  `"conversation"`, `"prep_item"`, `"invoice"`) to a `TargetSpec` — how to build
  the object's route, how to reach its engagement, and its default link label.
  Route paths live here once, so a frontend route rename is a one-line change.
* **`resolve_target(engagement, target_type, target_id)`**: the ownership gate.
  Rejects a target that doesn't exist, is malformed, or belongs to another
  client's engagement — a link can only point at the client's own data. Shared
  by `reminders` (a reminder's target) and `conversations` (the `links` array).
* **`build_url(obj)` / `default_label(obj)` / `type_for_instance(obj)`**: derive
  a stored target's route/label/slug on read (a deleted target resolves to
  `None`, so a stale link is dropped rather than served as a 404).
* **`absolute_url(path)` / `login_url()`**: turn a relative portal route into a
  full URL (built from `settings.FRONTEND_BASE_URL`) for use in emails, where a
  relative path is meaningless.

> **Frontend:** where a reminder/conversation carries a deep link, the API gives
> you a ready-to-use relative route and label — you don't build the URL. A
> reminder returns `link_url` + `link_label` (e.g.
> `"/portal/questions?conversationId=<uuid>"`); a conversation's `links[]`
> entries come back resolved to `{ label, url, target_type, target_id }`. Route
> it with your client-side router; a link whose target was deleted is simply
> absent from the response.

---

## Background Execution (`background.py`)

The replacement for the Celery broker — a bounded thread pool inside the web
process, plus the `@background_task` decorator every deferred task uses. Nothing
polls. Async dispatch is **opt-in per process and only `config/wsgi.py` opts in**;
everywhere else `.delay()` runs inline, which is what makes a cron sweep able to
send its own mail before exiting. Full reasoning, the durability invariant and the
five details that are easy to get wrong are in the module docstring and
[`docs/adr/0001-remove-celery.md`](../../docs/adr/0001-remove-celery.md).

Companion: `management/commands/run_scheduled.py` — the whole scheduler. One
cadence group per invocation, run to completion, exit; platform cron calls it.

---

## Recipient Timezones (`timezones.py`)

`local_today(user)` — today's calendar date **for that recipient**, and
`max_utc_offset_days()` — the constant the digests widen their queries by.

`TIME_ZONE` stays `UTC` and `USE_TZ` stays `True`: that is how *instants* should be
stored. But `PaymentMilestone.due_date` and `Meeting.date` are naive `DateField`s —
a calendar day in the client's world — and the daily digests compare against them.
Using `timezone.now().date()` there compares a client's due date against UTC's
today, which is a different day for anyone far enough from UTC, so a three-day
lookahead fires two or four days out. On a worldwide platform that is routine, not
a corner case.

Resolution order: `User.timezone`, else `settings.PLATFORM_DEFAULT_TIMEZONE`, else
UTC. Reads **fail soft** — an unknown zone name logs `event=unknown_timezone` and
degrades to UTC rather than raising, because one malformed row must not take out a
whole digest run. Writes fail loud (`User.clean()`, the API's `TimezoneField`), or
the soft fallback would hide the mistake for ever.

The digests use it in one shape worth knowing: widen the SQL window by
`max_utc_offset_days()` to get a guaranteed superset, then make the exact
per-recipient decision in Python. One query, exact answer — rather than a query
per distinct timezone.

What it deliberately does **not** do is change *when* a digest fires; that is
still one cron run at 08:00 UTC. Sending to each timezone at its own local 08:00
is a separate change, recorded as a follow-up in the ADR.

---

## Attribution (`models.AttributedModel` + `serializers` + `admin` + `utils`)

The shared "who did what, when" layer. Four pieces that are meant to be used
together — reach for all four when you add an attributed model, not just the
model mixin.

**1. `models.AttributedModel`** — abstract, adds `created_by` + `last_updated_by`
FKs to the user model. Combine with `TimestampedModel` (or
`UUIDTimestampedModel`) for the full who+when quartet:

```python
class Invoice(UUIDTimestampedModel, AttributedModel):   # timestamps + attribution
class Event(AttributedModel):                            # already has its own timestamps
```

Both FKs are `SET_NULL`, so deleting a staff account never cascades away the
records they touched — the row survives with the field nulled and the display
name falls back to `""`. Related names use `%(app_label)s_%(class)s_*` so the
reverse accessors stay unique across the ~20 models that inherit it.

**They are `editable=False`** — system-set from `request.user`, never typed.
Consequence for admin: they must go in `readonly_fields`, *never* in
`raw_id_fields` or an editable fieldset, or Django's system check errors.

**2. Stamping (`utils`)** — never assign the FKs by hand in a view:

```python
save_with_attribution(serializer, request.user, engagement=engagement)  # serializer path
stamp_attribution(instance, request.user, creating=False)               # service/ORM path
```

`save_with_attribution` sets `created_by` **and** `last_updated_by` on create,
and only `last_updated_by` on update (it keys off `serializer.instance is None`).
Both no-op for `AnonymousUser`, and `stamp_attribution` silently skips models
that don't carry the fields, so it's safe to call from shared helpers. Passing
the FKs as `save()` kwargs also means a client can't spoof them from the body.

**3. `serializers.AttributionSerializerMixin`** — mix in to the **left** of
`ModelSerializer`:

```python
class InvoiceSerializer(AttributionSerializerMixin, serializers.ModelSerializer):
```

It adds `created_by_display` / `last_updated_by_display` (names resolved at read
time via `user_display_name`, so they always reflect the account's *current*
name) **and strips the raw `created_by`/`last_updated_by` ids from the payload**.

That strip is the point: a `ModelSerializer` with `fields = '__all__'`
serialises a user FK as its bare integer pk, which is how responses used to
carry `last_updated_by: 1`. Marking the field read-only does **not** remove it —
only popping it in `to_representation` does.

> **If your serializer uses an explicit `fields` list**, you must add
> `"created_by_display"` and `"last_updated_by_display"` to it, or DRF raises
> *"declared on serializer … but has not been included in the `fields` option"*.
> Only `fields = '__all__'` picks them up automatically.

**4. `admin.AttributionAdminMixin`** — the structured who/when block:

```python
class InvoiceAdmin(AttributionAdminMixin, admin.ModelAdmin):
    list_display = (..., "created_at", "created_by_display", "updated_at", "last_updated_by_display")
    readonly_fields = (...,) + ATTRIBUTION_FIELDS
    fieldsets = (..., ATTRIBUTION_FIELDSET)
```

Use the `*_display` methods in `list_display` — the raw FK would render as the
user's **email** (`User.__str__`), not their name.

### Checklist for adding attribution to a new model
1. Inherit `AttributedModel` → `makemigrations` (an `AddField` per FK).
2. Stamp with `save_with_attribution` / `stamp_attribution` in every write path.
3. Mix `AttributionSerializerMixin` into the read serializer (+ add the two
   `*_display` names if the serializer uses an explicit `fields` list).
4. Mix `AttributionAdminMixin` into the admin + `ATTRIBUTION_FIELDS` /
   `ATTRIBUTION_FIELDSET`.
5. Regression-guard it — see `apps/core/tests.py::AttributionTests`, which pins
   the create-vs-update behaviour and the "no raw ids" contract.
