# Meetings & Preparation App

The `meetings` app handles the scheduling, management, and preparation workflows for client-staff consultations. It includes a robust preparation task checklist system allowing clients to answer questions and upload files before a meeting occurs.

---

## 💡 Core Ideology: Portal Phase vs. Meeting Phase

The portal phase (`current_phase` on `ClientPortal`) and individual meeting `phase` fields are distinct components that work together to structure the client journey:

### 1. Portal `current_phase` — The Client's Overall Journey Position
This is the single source of truth for where the client currently is in their planning process. It controls:
* **Progress Bar:** Highlights the active stage on the client's Overview page.
* **Q&C Default:** Surfaces relevant conversation threads in Questions & Choices.
* **Content Access:** Governs unlocked and locked content milestones.

### 2. Meeting `phase` — Organisation Label
This is a simple metadata tag indicating the planning phase in which the meeting belongs (e.g., "Connect" or "Curate"). It does **not** restrict client access; rather, it allows grouping, organizing, and filtering of meetings.

### 🌊 How They Work Together
* **Staff Action:** Staff advances the portal phase as the client progresses.
* **Historical View:** Individual meetings are tagged to the phase they were created in. All meetings (past, current, and future) remain fully accessible to the client regardless of the portal's current active phase.

---

## 🗄️ Models

* **`Meeting`**: Represents the consultation event. Tied to an `EventEngagement`, has a custom scheduled `date`/`time`, a `status` (upcoming, active, completed, cancelled, rescheduled), and a `phase` tag indicating which planning step it belongs to.
* **`MeetingPrepItem`**: A task group or checklist item that needs action before the meeting. Can be manually completed (if checkbox-only) or auto-completed when all fields are filled.
* **`PrepItemField`**: A defined input requirement within a prep item. Supports fields of type:
  * `checkbox` (Simple confirmation tick)
  * `file_upload` (Client registers a document/media)
  * `qa` (Short question & answer)
  * `text` (Freeform text inputs)
* **`PrepItemResponse`**: Stores client responses to a specific field. Linked via an upload path dynamically isolated under `portals/<portal_id>/meetings/<meeting_id>/prep/<field_id>/<filename>`.
* **`MeetingNotes`**: Shared post-meeting document summarizing discussion points, key decisions, and action items.

---

## ⚙️ Services

* **`transition_meeting_status(meeting, new_status)`**:
  Validates and updates the status of a meeting based on a state transition matrix:
  ```
  upcoming    → active | cancelled | rescheduled
  active      → completed | cancelled | rescheduled
  rescheduled → upcoming | cancelled
  ```
* **`submit_field_response(field, data, files=None, uploaded_by=None)`**:
  Saves a response for a prep item field. If the field is a `FILE_UPLOAD`, it creates the `PrepItemFileUpload`(s) and registers each file in the central **Document hub**. Re-syncs the item's completion afterward.
* **`field_is_answered(field)`**:
  A single field's own completion state — a file for `file_upload`, a ticked box for `checkbox`, non-empty text for `qa`/`text`. Applies to required and optional fields alike; used by both the completion gate and the API's per-field `is_completed` / answered counts.
* **`sync_prep_item_completion(prep_item)`**:
  Recomputes `is_completed` from current field state, in either direction. The gate: an item with **≥1 required field** is complete when every *required* field is answered (optional fields never block); an **all-optional** item has no required fields, so it is complete only when *every* field is answered (checklist behaviour). Called on item creation, after every response/upload change, and after any field add/remove/edit.
* **`update_prep_field(field, validated_data)`**:
  Applies a staff edit to a field. Changing `field_type` clears that field's existing answer (a text answer is meaningless once it's a file field, and vice versa) and returns `cleared_answer=True` so the view can warn. Always re-syncs completion.

> **Removed:** the old `complete_prep_item` (manual "mark as done"). Completion is now *always* derived from field state — a manually-set completion was a lie the next `sync` would silently revert. A "just acknowledge this" task is modelled as a required checkbox field instead.

---

## Completion is derived, never set by hand

A prep item's `is_completed` is computed, not stored-and-trusted: an item with
required fields completes when all *required* fields are answered; an
all-optional item completes when *all* its fields are answered. Each item
reports two counters plus a per-field flag:
```jsonc
"is_completed": false,
"required_fields": { "answered": 2, "total": 4 },   // the Figma "N of M"
"optional_fields": { "answered": 1, "total": 3 },   // informational only
"fields": [ { "id": "…", "is_required": true, "is_completed": true, … } ]
```

## Calendar export (`.ics`)

`GET /meetings/<id>/ics/` (staff or the meeting's portal owner) downloads a
single-`VEVENT` `.ics` file — `services.build_ics(meeting)`, stdlib-only, no
`icalendar` dependency needed for one event. This exists specifically for
Outlook/Apple Calendar's "Add to Calendar," which need a real file; **Google
Calendar's equivalent stays a frontend-only deep link** built from fields
already on the API (`date`, `time`, `duration_minutes`, `title`,
`meeting_url`) — no backend duplication of that.

- **No timezone conversion.** `config/settings.py` has `TIME_ZONE='UTC'` /
  `USE_TZ=True`, so `Meeting.date`/`time` already represent UTC wall-clock
  values — `DTSTART`/`DTEND` format straight to `YYYYMMDDTHHMMSSZ`.
- **`STATUS`** is `CANCELLED` when `meeting.status == MeetingStatus.CANCELLED`,
  `CONFIRMED` otherwise — a cancelled meeting's file, if already imported,
  can clear it from the client's calendar.
- **`UID`** is `f"{meeting.id}@hephluxe.com"` — stable across re-downloads
  (same domain convention as `apps/core/deeplinks.py`), so re-adding the same
  meeting updates the existing calendar entry instead of duplicating it.

## Validation rules worth remembering

* **File uploads** on a `file_upload` field are restricted to
  `application/pdf`, `image/jpeg`, `image/png`, `image/webp`, max **10MB** per
  file (`ALLOWED_PREP_UPLOAD_TYPES` / `MAX_PREP_UPLOAD_SIZE` in `services.py`)
  — chosen for inspiration boards/photos, not raw video. An oversized or
  disallowed file fails with `code=validation_error` naming the offending
  filename, not a generic rejection.
* **Adding a prep item with nested `fields` is atomic.** If field `[1]`
  (0-indexed) fails validation, the whole request is rejected —
  `errors: {"fields[1]": {...}}` — and **neither the prep item nor any of its
  fields are created**. This replaced an earlier version that saved the prep
  item, then looped over `fields` saving whichever ones happened to validate
  and silently dropping the rest.

---

## Prep items: creation, answering, and correcting an answer

### A prep item must be created **with at least one field**
`POST /meetings/<id>/prep/` rejects a field-less item with
**400 "A prep item must have at least one field."** Completion is derived purely
from fields, so an item with none could never be completed. The field(s) go
**nested under `fields`**, not at the top level — top-level `field_type`/`label`
are silently ignored by `MeetingPrepItemCreateSerializer`, which is the usual
cause of that 400:

```json
{
  "title": "Bring 3 reference images",
  "description": "For the reception backdrop",
  "order": 1,
  "fields": [
    { "field_type": "text", "label": "Backdrop colour preference", "is_required": true, "order": 1 }
  ]
}
```

Every nested field is validated **before** anything is created, so you never get
a 201 with half the fields silently dropped. Add further fields afterwards with
`POST .../prep/<item_id>/fields/` — *that* endpoint does take the attributes at
the top level.

### Answering, updating, and clearing
`POST .../fields/<field_id>/respond/` is the single write path (client **or**
staff — the only client-writable corner of meetings).

| Field type | Behaviour on re-submit |
|---|---|
| `text` / `qa` / `checkbox` | `update_or_create` — simply **overwrites** the previous answer |
| `file_upload` | **appends** by default (a second inspiration image shouldn't wipe the first) |

To correct a file upload you therefore need one of:

- `replace=true` on the respond call — swaps the whole set out in one request.
  New files are validated *first*, so a rejected upload never destroys the
  existing answer.
- `DELETE .../fields/<field_id>/uploads/<upload_id>/` — remove one file, keep the
  rest. `upload_id` is the **integer** `id` from the `uploads[]` array
  (`PrepItemFileUpload` still uses a `BigAutoField`); enumeration buys nothing
  because the lookup is scoped upload→field→item→meeting behind the ownership check.
- `DELETE .../fields/<field_id>/respond/` — clear the answer entirely (text or
  every file), putting the field back to unanswered.

All of them return the **updated prep item**, and every path re-runs
`sync_prep_item_completion` — deleting the last file on a required field flips
the item back to incomplete automatically.

**Deleting an upload is safe by design:** the `post_delete` receiver in
`signals.py` unregisters the `documents.Document` row and removes the blob, and
it's the one hook every delete path goes through (cascades from field/item/
meeting deletion never pass through view code at all).

### Reads
`GET /meetings/<id>/prep/<item_id>/` returns one item with its fields, responses
and uploads — **client-visible** (they need to refresh a single task group
without re-fetching the whole meeting). `PATCH`/`DELETE` on the same route stay
staff-only; the two verbs are gated separately inside the view.

Upload limits live in `services.py`: `ALLOWED_PREP_UPLOAD_TYPES`
(PDF/JPEG/PNG/WebP) and `MAX_PREP_UPLOAD_SIZE` (10 MB) — inspiration boards, not
raw video.
