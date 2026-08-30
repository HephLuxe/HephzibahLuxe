# Events App

The `events` app manages client event contexts and sub-events (Event Days). It forms the parent workspace context that wraps other client portal entities, such as meetings, tasks, and contacts.

---

## Models

### `Event`
The primary event workspace.
* **celebrant**: ForeignKey to `User` (the client who owns the event).
* **title**: CharField representation.
* **slug**: Unique SlugField auto-generated from `title`.
* **event_date**: DateField.
* **event_venue**: CharField.
* **event_type**: CharField containing `Birthday`, `Wedding`, `Corporate`, `Social Events`, or `Others`.
* **featured_image**: Main landing cover.
* **groom_name** / **bride_name**: Specific fields used if type is `Wedding`.
* **honoree_name**: Specific field used if type is `Birthday`.
* **event_name**: General string placeholder.

### `EventDay`
A scheduled sub-day or event milestone within the main Event.
* **owner**: ForeignKey to `Event`. Cascades on deletion.
* **event_day_title**: CharField title for the sub-day (e.g. "Traditional Wedding", "Reception").
* **date**: DateField of the day.
* **start_time** / **end_time**: Time bounds.
* **venue** / **venue_address**: Location details.
* **estimated_guest_count**: Positive integer target.

---

## Serializers

* **`EventSerializer`**:
  Handles serialization of the event workspace. Dynamically sanitizes conditional metadata fields based on the `event_type` parameter (e.g. pops `groom_name`/`bride_name` if the event is a `Birthday`).
* **`EventDaySerializer`**:
  Handles sub-day schedules. Exposes `venue_booking_status_display` (the field is
  a `VenueBookingStatus` enum: `confirmed` / `pending` / `not_booked`) and
  `last_updated_by_display`.

### Attribution ("Last Updated by …")
`Event` and `EventDay` carry a `last_updated_by` FK, set from `request.user` in
`update_event` / `update_eventday`; serializers expose `last_updated_by_display`
(the account's *current* name, via `apps/core/utils.py::user_display_name`,
shared with contacts and the planning phase).

### Client notification is debounced, not immediate
Updating an event or an event day calls
`services.schedule_event_details_notification(event, what)` — it does **not**
email the client right away. Instead it stamps three columns on
`event.engagement`:

| Column | Meaning |
|---|---|
| `event_details_notify_due_at` | when the debounce window closes. `NULL` = nothing pending. Indexed — the sweep's only query is "everything due at or before now" |
| `event_details_notify_what` | a human description of the most recent change, rendered in the email |
| `event_details_notify_token` | audit marker: *which* pending notification this is |

The window is `portal.PortalSettings.event_details_notify_debounce_seconds` —
**admin-configurable** (default 900s / 15 min), so staff can shorten or lengthen
it from the Django admin without a redeploy.

`events.tasks.dispatch_due_event_details_notifications` is the sweep that
actually sends. It runs from the `notification_retry` cron group (every 10
minutes — see `apps/core/management/commands/run_scheduled.py`).

**Why debounce:** staff editing several fields — or several event days — in one
sitting should trigger *one* email, not one per save. A fixed-delay timer alone
isn't enough either, since it would still pressure an editor to "finish before it
fires." So this is a **trailing debounce**: every edit simply pushes `due_at`
further out and overwrites `what`. As long as an admin keeps editing, nothing is
due and no email goes out. Once they stop for the full window, the next sweep
finds one due row and sends one email describing the most recent change.

**How the sweep avoids double-sending.** It clears the schedule **first**, in a
single `UPDATE … WHERE due_at IS NOT NULL`, and sends only if that UPDATE
actually claimed the row. Three properties fall out of that ordering:

* Two overlapping sweeps cannot both claim the same row (Render won't start a
  cron run while the previous one is going, but that is the platform's promise,
  not ours).
* A crash between the claim and the send loses one email rather than re-sending
  it on every sweep for ever.
* An edit landing between the claim and the send re-stamps its own later
  `due_at`, so that change gets its own email — which is exactly the debounce
  semantics.

`what` is read off the in-memory instance, which still holds the value the UPDATE
just blanked in the database. Deliberate: the row was claimed, so that is the
description belonging to *this* email.

#### Why this is three columns and not a countdown

It used to be one column plus `apply_async(countdown=900)`, and the task compared
the token it was handed against the engagement's current one — no-opping if
superseded. Elegant, but **only half durable**: the token lived in Postgres while
*"and send at T+900s"* lived in the broker's ETA queue and `what` lived only as a
task argument. A worker restart or a deploy inside the window could drop the email
outright, and recovery depended on Celery's `visibility_timeout` redelivery — an
implementation detail rather than a guarantee we owned. (`visibility_timeout` was
pinned to 3600s in settings specifically to stay above this countdown.)

Now the whole schedule is a row, so the next sweep sends it no matter what died in
between. The cost is precision: a 15-minute debounce swept every 10 minutes lands
15–25 minutes after the last edit rather than exactly 15. For "the planner
finished editing, tell the client" that is not a meaningful difference, and in
exchange the debounce survives a deploy. See
`docs/adr/0001-remove-celery.md`.

#### Toggles

The email is gated by the `event_details_updated` row in
`notifications.NotificationTypeSettings` like every other notification type (see
the `notifications` app). Separately, the whole debounce mechanism can be switched
off via the `event_details_notification` row in
`notifications.ScheduledTaskSettings` — when off,
`schedule_event_details_notification` doesn't stamp a due time at all, **and** the
sweep checks the same gate defensively, so a row stamped before the switch was
flipped doesn't fire either.

Falls back to sending immediately if the event has no engagement yet (no row to
key the debounce token on) — a rare case since engagements are normally created
alongside events.

---

## Aggregate detail endpoint

`services.build_event_detail(event)` assembles everything the Event Details
page needs in a single call (mirrors `document_hub.services.build_hub`) —
event + days + contacts (grouped by category, plus per-category summary
counts) + `planning_stage` for **this specific event's** engagement (not just
the portal's active one, which is what the plain portal-overview phase data is
scoped to). Response shape:

```jsonc
{
  "event": { /* EventSerializer */ },
  "event_days": [ /* EventDaySerializer */ ],
  "contacts": { "primary": { "label": "...", "contacts": [...] }, /* ... */ },
  "contacts_summary": [ { "category": "primary", "category_display": "...", "count": 2 }, /* ... */ ],
  "planning_stage": {
    "current_phase": "curate", "current_phase_display": "No. 03: Curate",
    "phase_index": 3, "total_phases": 6,
    "phase_details": {},
    "phase_updated_by_display": "Tosin O", "phase_updated_at": "2026-05-13T10:00:00Z",
    "event_details_locked": false, "contacts_locked": false
  } | null   // null when the event has no engagement yet
}
```

---

## Tips & gotchas

- **The slug is generated once and frozen.** It comes from the title on
  creation and is never regenerated on rename — a rename would break bookmarked
  portal URLs *and* orphan every uploaded file, since upload paths embed the
  slug. Upload paths are keyed on the immutable `event.pk` for the same reason.
- **`venue_booking_status` is a strict lowercase enum** — `confirmed`,
  `pending`, `not_booked`. A capitalised `"Confirmed"` is rejected with 400.
  Read it back via `venue_booking_status_display` ("Confirmed") rather than
  title-casing the raw value yourself.
- **Required name fields depend on `event_type`** (`services.generate_event_title`):
  Wedding needs `groom_name` **and** `bride_name`; Birthday needs `honoree_name`;
  Corporate / Social Events / Others need `event_name`. The title is always
  derived — never sent by the client.
- Those same name fields feed the Document Hub **reference-code segment**
  (`Priscilla & Samuel` + Wedding → `PSW###`), so getting them right at creation
  matters beyond the title. The segment is assigned once and frozen, like the slug.
- **Every event gets its own `EventEngagement` at creation**, active only if the
  portal has none active yet. That's what lets staff pre-stage a future event's
  meetings/documents before switching to it.
- `event_details_locked` (on the engagement) gates **client** edits to
  `Event`/`EventDay`; staff always pass. It's independent of `contacts_locked` —
  deliberately two flags, not one.
- **Deleting an event cascades widely.** Check `GET /event/<slug>/delete-impact/`
  first; `DELETE` refuses with `confirmation_required` unless `?confirm=true`
  when anything is attached.
