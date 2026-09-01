# Events App

The `events` app manages client event contexts and sub-events (Event Days). It forms the parent workspace context that wraps other client portal entities, such as meetings, tasks, and contacts.

---

## Models

### `Event`
The primary event workspace.
* **celebrant**: ForeignKey to `User` (the client who owns the event).
* **title**: CharField representation. **Derived**, never client-supplied — see `generate_event_title`.
* **headline**: Editorial headline for the public page. Free text, written by staff.
* **description**: Long-form narrative for the public page — the paragraphs under `headline`.
* **slug**: Unique SlugField auto-generated from `title`.
* **event_date**: DateField.
* **event_venue**: CharField.
* **event_type**: CharField containing `Birthday`, `Wedding`, `Corporate`, `Social Events`, or `Others`.
* **images**: Reverse relation to `EventImage` — the event's gallery. The cover is the `is_primary` row, read as `cover_image`. (There is no `featured_image` field; see *Galleries* below.)
* **groom_name** / **bride_name**: Specific fields used if type is `Wedding`.
* **honoree_name**: Specific field used if type is `Birthday`.
* **event_name**: General string placeholder.

### `EventDay`
A scheduled sub-day or event milestone within the main Event.
* **owner**: ForeignKey to `Event`. Cascades on deletion.
* **event_day_title**: Short label / eyebrow for the sub-day (e.g. "Traditional Wedding", "Pre-Birthday Photoshoot", "Event No. 1").
* **headline**: Editorial headline for this day.
* **content**: Long-form narrative for this day — the paragraphs under `headline`.
* **date**: DateField of the day.
* **start_time** / **end_time**: Time bounds.
* **venue** / **venue_address**: Location details.
* **venue_booking_status**: `VenueBookingStatus` enum — `not_booked` / `pending` / `confirmed`.
* **dress_code**: CharField (e.g. "Aso-Ebi — Deep Blue & Gold").
* **estimated_guest_count**: Positive integer target.

### `EventImage`
One photograph in a gallery. Serves both levels, told apart by `event_day`.
* **event**: ForeignKey to `Event`. **Always set**, even for a day image — it is what the storage path and the permission check read.
* **event_day**: ForeignKey to `EventDay`, nullable. NULL = an event-level image; set = one photograph in that day's gallery.
* **image**: The file. `max_length=500`, public storage, saved exactly as uploaded — nothing is resized or re-encoded.
* **alt_text**: Screen-reader description.
* **is_primary**: This gallery's cover. At most one per event and one per event day, enforced by two partial unique constraints.
* **sort_order**: Ascending display order; ties break on `created_at` so the order is total.

---

## Public-page copy: three fields, not two

Each event day renders three separate pieces of text, so it carries three text
fields whose boundaries are worth stating explicitly, because two of the three
used to be one undefined `content` column:

| Field | Renders as | Example |
|---|---|---|
| `event_day_title` | eyebrow above the headline | `PRE-BIRTHDAY PHOTOSHOOT`, `EVENT NO. 1` |
| `headline` | the headline | *A Moment Before Fifty — A Pre-Birthday Portrait Experience* |
| `content` | narrative paragraphs | *Before the celebrations began, there was a quiet moment to pause…* |

`Event` mirrors the same split one level up: `headline` + `description`. It has no
equivalent of the eyebrow — that line is composed from `country` / `state` /
`event_date` (`LAGOS, NIGERIA — 2021`).

**`event_day_title` deliberately stayed the short label** rather than being
promoted to the headline. Three other places interpolate it inline as a name —
the client notification (`views.py`), the contact-copy confirmation
(`contacts/views.py`) and the admin label (`contacts/admin.py`, `Day 1 —
Traditional Wedding`) — and an 80-character editorial headline reads badly in all
three.

**The `EVENT NO. n` numbering is typed, not computed.** Staff write the eyebrow as
it should appear. This looks like something `day_number` should derive, and it
isn't: `day_number` counts *every* day in date order, but a pre-birthday
photoshoot sorts first by date while sitting *outside* the numbered sequence — so
derived numbering renders the first celebration day as "No. 2". Making that work
would need a per-day "is this part of the sequence" flag; until staff are
observed getting the numbers wrong, the flag is not worth its weight. `day_number`
remains internal-only (it is not serialized).

**Existing short `content` values are fine.** Rows written before `content` had a
defined meaning hold one-liners like *"Traditional engagement ceremony with both
families."*. Those stay valid and simply render as a very short story — there is no
separate summary/teaser field, and no backfill was needed.

---

## Galleries: one model, two levels

`Event.featured_image` and `EventDay.event_images` are **gone**. Each held exactly
one image, which meant staff had one attempt at a cover and no way to keep
alternates, and a day's photographs had nowhere to live at all. Both are replaced
by `EventImage` rows:

| Level | `event_day` | Holds | Cover |
|---|---|---|---|
| Event | NULL | every image staff uploaded for the event | the `is_primary` row — the single tile the portfolio index renders |
| Event day | set | that day's full gallery, all of it rendered on the day's page | the `is_primary` row — the cover on the day's card |

Read the cover through **`cover_image`** on either serializer; it is the direct
replacement for the retired fields and saves a caller that only wants the cover
from fetching the whole gallery. `images` carries the gallery itself. The event's
`images` excludes day-level rows deliberately — otherwise every day photograph
would appear twice in one detail response.

**One model, not `EventImage` + `EventDayImage`.** The upload path needs the event
either way (paths are keyed `{event_id}-{slug}`), so a separate day model would
carry a redundant FK up to the event or re-walk `owner` on every path build. One
model also means one serializer, one route family and one blob-cleanup receiver.
The cost is that "belongs to this event" and "belongs to a day of this event" must
agree, which `EventImage.clean()` enforces.

### `is_primary` is a two-row operation

The partial unique constraints refuse a second primary outright, which is the
right floor but means a naive `image.is_primary = True; image.save()` raises
IntegrityError — the new primary is set before the old one is cleared, and the
constraint is checked in between. Everything that writes the flag goes through
`services.set_primary_image`, which clears then sets inside one transaction and
excludes the image being promoted so re-promoting the current cover is a no-op
rather than leaving the gallery coverless.

`services.ensure_gallery_has_a_cover` runs after every upload and delete, so the
first image uploaded becomes the cover automatically and deleting the cover
promotes the next one. Without it, "no images" and "images, but the primary was
deleted" look the same to the frontend and the second renders an empty tile.

### Endpoints

One route family for both levels — omit `event_day` for the event's own images,
pass it (query param on `GET`, body on writes) for a day's:

| Route | Method | Does |
|---|---|---|
| `/event/<slug>/images/` | `GET` | list a gallery — unpaginated, since the frontend needs all of it to lay out the grid |
| `/event/<slug>/images/` | `POST` | upload one or many (multipart, repeat the `image` key) |
| `/event/<slug>/images/<id>/` | `PATCH` | `alt_text`, `sort_order`, or `is_primary: true` to promote |
| `/event/<slug>/images/<id>/` | `DELETE` | remove the row and its blob |
| `/event/<slug>/images/reorder/` | `POST` | `{"image_ids": [...]}` — set the whole order at once |

Reorder is one call rather than N `PATCH`es because a drag-and-drop changes every
position simultaneously: sent one at a time the gallery passes through orders
nobody asked for, and a failure halfway leaves it scrambled with no way to tell
which half applied. The list must name every image in the gallery exactly once —
a partial list is a 400.

Writes are gated by `event_details_locked` exactly like editing the event itself;
a locked event whose photographs are still replaceable isn't locked. Unlike
`delete_eventday`, a client **may** delete their own images when unlocked — a
photograph is content they supplied, and the day it hangs off survives.

### Storage paths and cleanup

Gallery paths embed the image's own id and keep the uploaded filename:

```
portals/{portal}/events/{event_id}-{slug}/gallery/{image_id}/{filename}
portals/{portal}/events/{event_id}-{slug}/days/{day_id}/gallery/{image_id}/{filename}
```

The retired fields resolved to a *constant* name (`covers/cover.jpg`). Fine for
one file; for N rows every path would collide and the storage backend would
quietly append a random suffix to each, leaving blobs with no stable mapping back
to a row. The id segment fixes that, and matches how `prep_upload_path` and the
document hub already handle multi-row files.

`apps/events/signals.py` deletes the blob on `post_delete`, deferred to
`transaction.on_commit` because storage is not transactional. **Neither retired
field had this** — deleting an event left its cover in the bucket for ever.
Registering the receiver also forces Django off its fast-delete optimisation, so
it fires on cascades (`Event.delete()` → both galleries, `EventDay.delete()` →
that day's) which never pass through view code.

Migration `0012_migrate_single_images_to_gallery` carries any existing
`featured_image` / `event_images` value across as the primary before dropping the
columns, and is reversible. It moves no blobs — it repoints the new row at the
path the file already occupies, so those carried-over rows keep the old layout
for ever, which is harmless.

---

## Serializers

* **`EventSerializer`**:
  Handles serialization of the event workspace. Dynamically sanitizes conditional metadata fields based on the `event_type` parameter (e.g. pops `groom_name`/`bride_name` if the event is a `Birthday`).
* **`EventDaySerializer`**:
  Handles sub-day schedules. Exposes `venue_booking_status_display` (the field is
  a `VenueBookingStatus` enum: `confirmed` / `pending` / `not_booked`) and
  `last_updated_by_display`.
* **`EventImageSerializer`**:
  One gallery image. `event` / `event_day` are read-only — the scope comes from
  the URL and the request body, resolved and membership-checked in the view, so
  a caller can't attach an image to an event they can't reach by putting an id in
  the body.
* Both `EventSerializer` and `EventDaySerializer` mix in `_GalleryMixin` for
  `images` + `cover_image`. Both read `obj.images.all()` rather than `.filter()`
  so a `prefetch_related("images")` is actually used — the difference between one
  query and one per row on a list endpoint. The querysets in `views.py` and
  `build_event_detail` prefetch accordingly.

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
  "event": { /* EventSerializer — incl. images[] + cover_image */ },
  "event_days": [ /* EventDaySerializer — each incl. images[] + cover_image */ ],
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
  when anything is attached. The preview counts `event_images` — one figure for
  both galleries, since every `EventImage` cascades from `event` whether or not
  it also names a day.
