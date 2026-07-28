# Hephzibah Luxe — API Contract (v1)

> **Not a route list.** Exact paths and HTTP methods live in each app's
> `urls.py` (every `path()` there carries a `# GET`/`# POST`/etc. comment) —
> that file is the source of truth for routing, and duplicating it here would
> just be a second place to go stale. This document holds the **cross-cutting
> conventions** every endpoint follows, plus a pointer to where each app's
> *behavioral* design decisions are written up. Per-app logic (what a field
> means, why a workflow is shaped the way it is, how one app's data links to
> another's) lives in that app's `README.md`.

## Conventions

- **Base:** every route is served under **`/api/v1/`**. The Django admin
  (`/admin/`) is the only thing outside the prefix.
- **Auth:** JWT (SimpleJWT) in `Authorization: Bearer <access>`. `access` lives
  1 day, `refresh` 7 days (rotating, blacklist-on-rotate).
- **Roles:** `client` (celebrant) sees only their own portal's resources; `staff`
  / `admin` may act on any portal, usually by passing `?portal_id=<uuid>` (reads)
  or `portal_id` in the body (writes).
- **Success:** the serialized object, or a list (pagination is being migrated
  per endpoint — see `apps/core/pagination.py`. Two strategies coexist:
  `StandardCursorPagination` for unbounded lists, `StandardPageNumberPagination`
  where the UI itself is numbered-page shaped, e.g. Budget Payment History).
- **Error envelope:** `{ "detail": "...", "code": "machine_code", "errors?": {field: [..]} }`
  on **every** error response project-wide — both exceptions raised via `enforce()`
  / `get_object_or_404` (auto-enveloped by `apps.core.exceptions.custom_exception_handler`)
  and manually-constructed error responses (each `views.py` has a local `_error()`
  helper). Unhandled 500s return `code=internal_error`. Codes are defined in
  `apps/core/error_codes.py`.
- **IDs in URLs are UUIDs**, project-wide, for anything that's a URL path
  parameter — including `EventDay`, `Meeting`, `MeetingPrepItem`, `PrepItemField`,
  and `Conversation` IDs (converted from sequential integers in Phase 6).

## Where app-specific behavior is documented

| App | README | What it covers |
|---|---|---|
| `accounts` | `apps/accounts/README.md` | JWT flow, admin-driven registration + forced password change, 3-phase password reset. |
| `portal` | `apps/portal/README.md` | Portal phase vs. meeting phase, `EventEngagement` lifecycle, default team-member seeding, phase attribution + auto-lock. |
| `events` | `apps/events/README.md` | Event/EventDay model, attribution, the `event_details_updated` Celery debounce, aggregate detail response shape. |
| `contacts` | `apps/contacts/README.md` | Category grouping, day-pinning design, contacts lock. |
| `meetings` | `apps/meetings/README.md` | Prep item/field completion derivation, file-upload validation, atomic nested-field creation. |
| `conversations` | `apps/conversations/README.md` | Tag/link validation, deep-link pills. |
| `reminders` | `apps/reminders/README.md` | Deep-link target registry, email CTA. |
| `document_hub` | `apps/document_hub/README.md` | Auto-seeded portal defaults, percentage-driven payment split, auto-generated reference codes. |
| `documents` | `apps/documents/README.md` | Generic-FK document registry pattern shared across apps. |
| `budgets` | `apps/budgets/README.md` | Budget/category/payment model, receipt registration, Payment History pagination shape. |
| `notifications` | `apps/notifications/README.md` | `queue_notification` dispatch, per-type toggles, retry/cleanup tasks, beat schedule. |
| `core` | `apps/core/README.md` | Shared deep-link target registry (`apps/core/deeplinks.py`), base models, permissions. |
