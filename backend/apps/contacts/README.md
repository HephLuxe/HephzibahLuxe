# Contacts App

## Overview

The contacts app manages **`EventContact`** records — essentially a curated address book for each event. Staff adds people (bride, groom, family VIPs, key vendors, etc.) so both staff and the client can see who's involved and how to reach them.

---

## The Model — `EventContact`

The single model stores everything about one contact:

| Field | Purpose |
|---|---|
| `event` | FK to `events.Event` — every contact belongs to an event |
| `event_day` | Optional FK to `events.EventDay` — pin a contact to a specific sub-event (e.g. "Traditional Wedding") |
| `category` | One of 5 enum values — determines how contacts are grouped |
| `name`, `role` | e.g. `"Adaeze Obi"` / `"Client – Bride"` |
| `phone`, `email`, `preferred_method` | Contact info + how they prefer to be reached (WhatsApp / Email / Phone) |
| `photo` | Profile photo, stored at `portals/<portal_id>/contact_photos/<filename>` |
| `last_updated_by` | Staff member's name, written as a plain string (not a FK) |

**The 5 categories** (`ContactCategory`):
1. `primary` — Primary Contacts
2. `decision_maker` — Decision Makers & Approvals
3. `family_vip` — Family & VIP Representatives
4. `key_participant` — Key Participants
5. `event_day` — Event-Day Contacts

**Uniqueness constraint:** No two contacts in the same `(event, event_day, category, email)` combo — but only enforced when email is non-empty.

---

## How It Chains Through Other Apps

```
User (accounts)
 └── ClientPortal (portal) ← 1:1
      └── Event (events)  ← ForeignKey (celebrant = user)
           ├── EventDay (events) ← FK (owner = Event)
           └── EventContact (contacts) ← FK (event + optional event_day)
```

The `get_portal_id()` method on `EventContact` traverses this chain:

```python
# contacts/models.py
def get_portal_id(self):
    return self.event.celebrant.portal.id
```

This is used by `core/utils.py` to compute the media upload path:

```
portals/<portal_id>/contact_photos/<filename>
```

So every photo is stored under the correct client's portal folder in media storage.

---

## Contacts are pinned to a day by design

A contact belongs to one `event_day` (or `None`, meaning "shared across the
whole event") — there's no "belongs to multiple days" option. If the same
person needs to appear under a different day, staff use `copy_contacts_from_day`
(the "same as" action) to duplicate them rather than re-pointing the original.
This keeps each day's contact list independently editable without a shared
row's edits leaking across days.

## Contacts lock

Once `EventEngagement.contacts_locked` is set, clients can no longer add/edit
their own contacts — every write attempt returns `code=contacts_locked`
(`apps.core.error_codes.CONTACTS_LOCKED`). Staff can always write regardless of
the lock. The lock can be toggled manually by staff, or automatically once a
configured planning phase is reached — see `apps/portal/README.md`'s "Phase
attribution & auto-lock" section.

### `list_contacts` — grouped response

The list endpoint returns a **dict keyed by category**, not a flat array. This makes it immediately usable by the frontend without any client-side sorting:

```json
{
  "primary": {
    "label": "Primary Contacts",
    "contacts": [...]
  },
  "event_day": {
    "label": "Event-Day Contacts",
    "contacts": [...]
  }
}
```

Only categories that actually have contacts are included in the response (empty categories are omitted).

**Optional query params:**
- `?event_day_id=<id>` → returns day-specific contacts **plus** shared (no-day) contacts via a `Q` OR filter
- `?category=<value>` → further narrows to one category

### `create_contact` — staff only, cross-validates the event day

After serializer validation, the view does an extra safety check:

```python
if event_day and event_day.owner != event:
    return 400  # "This day does not belong to the specified event"
```

This prevents a bug where someone passes an `event_day` that belongs to a *different* event. The serializer can't catch this alone because `event` comes from the URL, not the request body.

---

## Serializers

Following the one-serializer-per-use-case rule, there are three:

| Serializer | Used for | Notes |
|---|---|---|
| `EventContactSerializer` | Single contact reads + response after create/update | Full fields including `event`, `created_at`, `updated_at` |
| `EventContactListSerializer` | List view (grouped by category) | Lighter — no `event` FK, no timestamps |
| `EventContactCreateSerializer` | Input for POST and PATCH | Adds `validate()` to catch duplicate `(event, event_day, category, email)` combos; `event` is excluded (comes from URL) |

---

## Permission Model

The contacts app uses the **functional permission helpers** from `core/permissions.py` rather than the class-based `IsPortalOwner`:

```python
from ..core.permissions import IsStaffOrSuperuser, can_access_event, enforce

# list / GET detail — staff or the event's celebrant
enforce(can_access_event(request.user, event))

# PATCH / DELETE — staff only
enforce(request.user.is_staff or request.user.is_superuser, "Only staff can modify contacts.")
```

`enforce()` raises `PermissionDenied` (403) instead of returning a bool — it's a shorthand that avoids the manual `if not ...: return Response(403)` pattern.

---

## Design Decisions & Gotchas

1. **No `services.py`** — all logic sits directly in views. This is consistent with the legacy apps. If complexity grows (e.g. sending notifications when a contact is added), extract to a `services.py`.

2. **`last_updated_by` is a plain string**, not a FK to the staff user. It's a display label, not a queryable relationship. It's constructed in the view as `f"{first_name} {last_name}".strip() or email`.

3. **`event_day` is truly optional.** A contact with `event_day=None` is a *shared* contact for the whole event. The `?event_day_id=` filter returns both shared + day-specific contacts in one call — the frontend doesn't need two requests.

4. **Photo upload path traverses 3 hops** (`contact → event → celebrant → portal`). If any link in that chain is `None` (e.g. `celebrant` is null on an event), `get_portal_id()` will raise an `AttributeError`. Worth guarding if contacts can ever be attached to events without a celebrant.

5. **URLs have no prefix** — mounted with `path('', include('apps.contacts.urls'))` in `config/urls.py`. The full API path is `/event/<slug>/contacts/`, not `/contacts/event/<slug>/contacts/`.
## Attribution note

`last_updated_by` is no longer contacts-specific: every write model in the
project now carries `created_by` **and** `last_updated_by` via
`core.AttributedModel`, exposed as `created_by_display` /
`last_updated_by_display`. The raw FK ids are **stripped** from responses by
`AttributionSerializerMixin` — if you're looking for `last_updated_by: 1`, it's
gone on purpose (see `apps/core/README.md`). The reasoning recorded here for
using an FK over a frozen name string (FAILURE_POINTS_AUDIT F11) is what the
project-wide layer was modelled on.

Note `copy_contacts_from_day` stamps the copying staff member as **both**
`created_by` and `last_updated_by` on the new rows — the copies are new records
authored by whoever ran the copy, not by whoever created the originals.
