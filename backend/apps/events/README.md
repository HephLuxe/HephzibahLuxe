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
email the client right away. Instead it stamps a fresh UUID token onto
`event.engagement.event_details_notify_token` and schedules
`events.tasks.send_event_details_updated_notification_task` to run after
`portal.PortalSettings.event_details_notify_debounce_seconds` — **admin-
configurable** (default 900s / 15 min), so staff can shorten or lengthen the
window from the Django admin without a redeploy.

**Why:** staff editing several fields — or several event days — in one sitting
should trigger *one* email, not one per save. A fixed-delay timer alone isn't
enough either, since it would still pressure an editor to "finish before it
fires." So this is a **trailing debounce**: every edit re-stamps the token and
reschedules the send. When a scheduled task finally runs, it checks whether the
engagement's *current* token still matches the one it was given —

* **matches** → no edit has happened since this task was scheduled → this was
  the last edit in the burst → send, then clear the token.
* **doesn't match** → a later edit already replaced the token and scheduled its
  own (later) task → this task is stale → no-op silently.

The practical effect: as long as an admin keeps editing, no email goes out and
there's no deadline to race. Once they stop for the full debounce window, one
email fires describing the most recent change. The email is gated by the
`event_details_updated` row in `notifications.NotificationTypeSettings` like
every other notification type (see the `notifications` app). Separately, the
whole debounce/scheduling mechanism itself can be switched off via the
`event_details_notification` row in `notifications.ScheduledTaskSettings` —
when off, `schedule_event_details_notification` doesn't even stamp a token or
schedule a task, and an already-scheduled task no-ops defensively if toggled
off mid-flight.

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
