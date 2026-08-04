# reminders

The **Reminders** widget (sidebar, every portal page). A reminder is a
staff-authored, client-facing to-do scoped to an engagement.

Built to the conventions in `HEPHZIBAH_LUXE_AUDIT_AND_PLAN.md` Part 7 — this app
is the **reference implementation** of the standard error envelope
(`{detail, code, errors?}`), `core` base models, thin views + `services.py`, and
type-annotated helpers. All routes are mounted under `/api/v1/`.

## Model — `Reminder`
Inherits `core.models.UUIDTimestampedModel` (UUID PK + timestamps).

| Field | Notes |
|---|---|
| `engagement` | FK → `portal.EventEngagement` (nullable, like meetings/conversations). |
| `title` / `description` | Card heading + body. |
| `priority` | `high` / `medium` / `low`. Sorted by numeric weight (`PRIORITY_WEIGHT`), not alphabetically. |
| `due_date` | Optional. |
| `is_completed` / `completed_at` | Toggled by the client; `completed_at` stamped on completion. |
| `target_content_type` / `target_object_id` | GenericFK (`target`) to the object the reminder is about — see Deep links below. |
| `link_url` / `link_label` | Fallback for links with no object behind them (static page, external URL). `link_label` also overrides a target's default label. |
| `order` | Manual sort weight. |
| `created_by` | FK → user (SET_NULL). |

## Deep links

A reminder points at an **object**, not a URL string. Staff say *what* it is
about (`target_type` + `target_id`); the route is derived on read from the
registry in `apps/core/deeplinks.py`.

```jsonc
// POST /reminders/create/
{
  "portal_id": "…",
  "title": "Approve final run-of-show",
  "priority": "low",
  "due_date": "2026-07-08",
  "target_type": "conversation",
  "target_id": "c6e509f6-…"
}

// GET /reminders/ — link_url and link_label are derived, not stored
{
  "target_type": "conversation",
  "target_id": "c6e509f6-…",
  "link_url": "/portal/questions?conversationId=c6e509f6-…",
  "link_label": "View conversation"
}
```

`target_type` is one of: `conversation`, `meeting`, `prep_item`, `event`,
`event_day`, `event_contact`, `client_document`, `invoice`, `receipt`,
`payment_milestone`, `budget_payment`. Add a route by adding an entry to
`TARGET_TYPES` — nothing else changes.

Why an object rather than a typed URL (`services.resolve_target`):

* **It can't point at another client's data.** Every target reports its
  engagement; a target outside the reminder's own engagement is rejected at
  create/edit time. A hand-typed `?conversationId=<uuid>` is unverifiable —
  the backend would hand the client someone else's id without noticing.
* **It can't point at a row that doesn't exist.** Bad/malformed ids are
  rejected up front; a target deleted *later* makes `link_url` resolve to
  `null`, so the card renders without a link rather than 404-ing on click.
* **Route renames are a one-line change** in `deeplinks.py`, not a data
  migration over stored strings.

Sending `"target_type": null` on `PATCH` clears the target. Reminders created
before targets existed keep working — they fall through to `link_url`.

The same targets back the deep-link pills on a **conversation** (`links`) — the
registry and `deeplinks.resolve_target` are shared.

### Emails

The "new reminder" email includes the deep link as a CTA button, made absolute
via `deeplinks.absolute_url()` + the **`FRONTEND_BASE_URL`** setting (a relative
route is not clickable from an inbox). That setting is **required** — it is
never defaulted in code, so a misconfigured deploy fails at boot rather than
quietly mailing clients a link to the wrong environment. A reminder with no
target and no `link_url` simply renders without a CTA.

## List behavior worth remembering

`?status=pending|completed|all` (default `pending`) and
`?sort=priority|due|newest|oldest` (default `priority` — high before medium
before low, ties broken by soonest due date, not alphabetical, since
`ReminderPriority` is a `TextChoices` and Django would otherwise sort
"high" < "low" < "medium" alphabetically).

Empty state ("You're All Caught Up") is a frontend concern — the list endpoint
simply returns `[]` when nothing is pending (including when the portal has no
active engagement).

## Tests
`python manage.py test reminders` — staff-create, priority sort, client-complete,
cross-tenant isolation, empty state.

---

## Tips & gotchas

- **`created_by` now has a sibling.** `Reminder` moved onto
  `core.AttributedModel`, so it carries `last_updated_by` too — stamped on edit
  and on completion toggles. `ReminderSerializer` exposes both as
  `created_by_display` / `last_updated_by_display`.
- **Completion is a client action.** `PATCH /reminders/<id>/complete/` is open to
  the owning client as well as staff (most other reminder writes are staff-only),
  and it records who toggled it.
- **Deep links are derived, not stored.** Point a reminder at an object
  (`target_type` + `target_id`) and let `core.deeplinks` build the URL — that's
  what guarantees the link resolves to a real row **belonging to this
  engagement**. A hand-typed `link_url` can silently carry another client's id;
  it exists only for links with no object behind them. Send
  `"target_type": null` to clear a target on edit.
- **The "new reminder" email is immediate**, unlike the payment-due and
  meeting-prep digests which are periodic lookahead scans — a new reminder has a
  clear trigger moment to hang a notification off.
