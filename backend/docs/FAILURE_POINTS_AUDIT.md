# Failure Points & Areas for Improvement — Codebase Audit

**Date:** 2026-07-12 (last status update: 2026-07-12)
**Scope:** Full backend (`apps/*`) — read of every models/views/services/serializers/signals file.
**Trigger:** The engagement cascade-delete data loss discovered when two events were created and one
deleted. This audit hunts for the *same classes* of latent failure elsewhere: silent cascade deletes,
once-only auto-create logic, swallowed exceptions, nullable-FK orphaning, PK-type mismatches, and
permission blast radius.

Severity legend: **P0** = silent data loss / catastrophic; **P1** = correctness bug or orphaning;
**P2** = edge-case robustness; **P3** = consistency/polish.

## Status at a glance

| # | Finding | Status |
|---|---|---|
| F1 | `Event.delete()` cascade-wipes everything, no warning | ✅ **Fixed** — impact-preview + `?confirm=true` |
| F2 | Client can trigger that destruction | ✅ **Fixed** — deletes are staff-only; edits gated by `event_details_locked` |
| F3 | Engagement auto-create only fires for the first event | ✅ **Accepted as intended behavior** — not a bug, no fix planned (see note) |
| F4 | GenericFK PK-type mismatch (UUID into `PositiveIntegerField`) | ✅ **Fixed** — `object_id` is now `CharField` |
| F5 | Switching active event silently hides prior content | ✅ **Fixed** — response now includes a content summary + note |
| F6 | Contact copy aliases one photo file across rows | ✅ **Fixed** — copies actual file bytes now |
| F7 | `create_event` silently swallows missing-portal case | ✅ **Fixed** — now logs a warning |
| F8 | Portal auto-creation keyed on `role == "client"` every save | ✅ **Accepted as intended behavior** — not a bug, no fix planned (see note) |
| F9 | Slug uniqueness never released, accumulates `-N` | ✅ **Complete** — storage-path risk removed; no reclaim, by deliberate choice (see note) |
| F10 | `create_event` race on the portal's first active engagement | ✅ **Fixed** — see corrected note below |
| F11 | `last_updated_by` is a free-text name, not a user FK | ✅ **Fixed** — now a `User` FK, matching `Reminder.created_by` |
| F12 | Delete endpoints return `200` not `204` | ✅ No action needed — intentional per CLAUDE.md §13 |
| F13 | `Notification.engagement` is `SET_NULL`, loses context | ✅ No action needed — accepted design choice |

---

## P0 — Data loss & destructive blast radius

### F1. `Event.delete()` silently cascade-wipes the entire engagement subtree — ✅ FIXED
**Where:** [events/views.py:161](../apps/events/views.py#L161) (`event.delete()`), cascades defined across
`portal`, `meetings`, `conversations`, `reminders`, `documents`, `document_hub`, `budgets`, `contacts`.

Deleting one `Event` row destroys ~15 model types across 7 apps, with no confirmation, preview, or
recovery:

```
Event.delete()
├── EventDay                         (owner CASCADE)  → EventContact (event_day CASCADE)
├── EventContact                     (event CASCADE)
├── EventBudget (OneToOne CASCADE)   → BudgetCategory, BudgetPayment
└── EventEngagement (OneToOne CASCADE)
      ├── Meeting → MeetingPrepItem → PrepItemField → PrepItemResponse / PrepItemFileUpload
      │            └── MeetingNotes
      ├── Conversation
      ├── Reminder
      ├── Document
      ├── ClientDocument
      ├── PaymentSchedule → PaymentMilestone
      ├── Invoice
      └── Receipt
      (Notification survives via SET_NULL but loses its engagement link)
```

This is the bug that started the audit. **Recommended fix:** impact-preview + explicit confirmation on
the delete endpoint (return counts of what will be destroyed; refuse unless `?confirm=true`). Soft-delete
is the heavier alternative but collides with the `slug` unique constraint — see F9.

**Fix shipped:** `events/services.get_event_deletion_impact()` counts everything the delete would cascade
through. `delete_event` now refuses with a `400 CONFIRMATION_REQUIRED` (body includes the full impact
breakdown) unless `?confirm=true` is passed, but skips the friction entirely when the event is genuinely
empty. A standalone `GET /event/<slug>/delete-impact/` previews the same breakdown without deleting
anything. Verified against a real event with a live engagement (3 event days, 10 contacts → correctly
blocked without `confirm=true`).

### F2. A **client** can trigger that destruction on their own event — ✅ FIXED
**Where:** [events/views.py:146-162](../apps/events/views.py#L146-L162).

`delete_event` is guarded only by `@permission_classes([IsAuthenticated])` + `enforce(can_access_event(...))`.
`can_access_event` ([core/permissions.py:33-35](../apps/core/permissions.py#L33-L35)) returns True for the
event's **celebrant** — i.e. the client. So a client can delete their own event and thereby wipe
staff-authored invoices, receipts, payment schedules, meeting notes, and documents (F1's blast radius).
Same applies to `delete_eventday` and `update_event` (client can rename/rewrite their own event).

**Recommended fix:** make destructive event operations staff-only (`IsStaffOrSuperuser`), or at minimum
gate `delete_event` behind staff. Client-facing writes should be limited to the explicitly-client actions
(prep items, contacts when unlocked, reminder completion).

**Fix shipped:** `delete_event` and `delete_eventday` are now unconditionally staff-only
(`IsStaffOrSuperuser`), regardless of any lock state — deletion carries the cascade risk (F1), so it's an
absolute rule, not a toggle. `update_event`/`update_eventday` stay client-accessible but are now gated by a
new, independent `event_details_locked` flag on `EventEngagement` (separate from `contacts_locked` — locking
one does not lock the other), toggled via staff-only `PATCH /event/details-lock/`.

### F3. Engagement auto-create fires **only for a portal's first-ever event** — the "no active event" trap — ✅ ACCEPTED AS INTENDED
**Where:** [events/views.py:60-66](../apps/events/views.py#L60-L66).

```python
if not portal.engagements.filter(is_active=True).exists():
    EventEngagement.objects.create(portal=portal, event=event, is_active=True)
```

Every event after the first (or any event created while another engagement is active) gets **no
engagement of its own**. Because `getPortal` reads everything through `active_engagement`, such an event is
invisible in the portal — no event, no phase, no meetings. This is exactly the state the recent incident
landed in after the original engagement was cascade-deleted (F1) and the surviving `-1` event had never
received one.

**Decision: this is intended behavior, not a bug.** A portal has one active engagement at a time by design;
a 2nd+ event is meant to stay inactive until staff deliberately switches to it — that's the normal
"multiple events over a client's lifetime" flow, not an accident to prevent. No further change planned here.

**What did ship as a result of this investigation:** a `PATCH /portal/activate-event/` endpoint
([portal/views.py](../apps/portal/views.py), `activate_event`) that wires up the previously-dead
`services.activate_engagement` — staff can now actually switch which event is active for a portal, which
had no API before this. That's a real, standalone feature, not a "fix" for F3 specifically.

**Addendum (2026-07-13) — pre-staging a future event:** follow-up question surfaced a real gap: while an
event had *no* engagement until activated, staff couldn't add meetings/conversations/reminders/documents to
a future event ahead of switching to it — there was nothing to attach them to. Owner decided this should be
possible. Shipped:
- `create_event` ([events/views.py](../apps/events/views.py)) now creates an `EventEngagement` for **every**
  event, not just the first — `is_active=True` only if the portal has no other active engagement yet,
  `False` otherwise. A race between two concurrent creations both trying for `is_active=True` now falls back
  to creating the loser's engagement as inactive (rather than the previous "engagement-less" outcome).
- `create_meeting`, `create_conversation`, `create_reminder`, and all four document_hub create endpoints
  (`create_document`, `create_payment_schedule`, `create_invoice`, `create_receipt`) now accept an optional
  `engagement_id` to target a specific (possibly inactive) engagement — defaulting to `active_engagement`
  when omitted, so existing callers are unaffected.
- Verified end-to-end: Event B created while A is active correctly gets an inactive engagement; the
  race-recovery path correctly falls back to inactive; omitting `engagement_id` still lands on the active
  engagement (A); passing B's `engagement_id` correctly targets B and the two engagements' content stays
  isolated; and — the actual payoff — later calling `activate_engagement(portal, event_b)` correctly
  **reuses** B's existing engagement (via `get_or_create`) rather than creating a fresh one, so pre-staged
  content survives the switch. Restored the real portal to its original active event afterward.

---

## P1 — Correctness & orphaning

### F4. GenericFK PK-type mismatch in the documents registry (latent) — ✅ FIXED
**Where:** [documents/models.py:26](../apps/documents/models.py#L26), [documents/services.py](../apps/documents/services.py).

`Document.object_id` is a `PositiveIntegerField`, and `register_document` does
`object_id = source_instance.pk`. But the model comment and `DocumentCategory` choices explicitly invite
registering **UUID-PK** sources — `EventDay`, `EventContact` (both UUID PKs), plus `EVENT_COVER` /
`CONTACT_PHOTO` / `TEAM_PHOTO` categories. Registering any of those would try to store a UUID in a
`PositiveIntegerField` → `ValueError`/data corruption.

Currently only `PrepItemFileUpload` (int PK) is wired ([meetings/services.py:83](../apps/meetings/services.py#L83)),
so it's dormant — but the categories are defined and the docstring says "Event, EventDay, EventContact,"
so it's a trap primed for the next person who wires up cover/contact/team-photo registration.

**Recommended fix:** change `object_id` to `CharField`/`UUIDField`-compatible (Django's convention for
mixed-PK GenericFK is `CharField`), or drop the aspirational categories until the store supports them.

**Fix shipped:** `object_id` changed to `CharField(max_length=255)` (migration `0005_alter_document_object_id`).
No changes needed in `register_document()` — Django's `CharField.get_prep_value` stringifies whatever's
assigned automatically. Verified end-to-end against a real `EventContact` (UUID PK): registered, reloaded
fresh from the DB, `GenericForeignKey` resolved correctly back to the original contact. Confirmed existing
rows (previously int values) migrated cleanly to their string equivalents with no data loss.

### F5. Switching the active event silently hides all prior-engagement content — ✅ FIXED
**Where:** [portal/services.py:81-93](../apps/portal/services.py#L81-L93) + the new activate-event endpoint.

`activate_engagement` deactivates the current engagement and activates another. Because meetings,
conversations, reminders, documents, invoices, and receipts all hang off `active_engagement`, switching
events makes **all** of the previous engagement's content vanish from the client view at once. This is
intended per the portal README ("historical data … no longer surfaced"), but there's no warning and it's
easily conflated with data loss (it's recoverable by switching back). A mis-click on activate-event looks
identical to F1's real deletion from the client's side.

**Recommended fix:** on the activate-event response, include a summary of what's moving out of view, and
document clearly that switching is reversible (unlike delete).

**Fix shipped:** `portal/services.get_engagement_content_summary()` counts meetings, conversations,
reminders, documents, client_documents, invoices, receipts, and payment milestones on an engagement.
`activate_event` ([portal/views.py](../apps/portal/views.py)) now captures the previous active engagement's
content *before* switching, and — only when actually moving away from a different, already-active event
(not when re-activating the same one, and not when there was no prior active engagement) — includes
`previous_engagement_content` (the counts) and a `note` explaining it's non-destructive and reversible.
Verified end-to-end: switching to a new event correctly returned the "is switching away" flag and a content
summary; re-activating the same event correctly suppressed it; switched back and confirmed the real portal's
active event was restored to its original state with no side effects.

### F6. Contact copy shares one photo file across multiple DB rows — ✅ FIXED
**Where:** [contacts/views.py:281-292](../apps/contacts/views.py#L281-L292) (`copy_contacts_from_day`).

`EventContact.objects.create(..., photo=contact.photo, ...)` copies the *file reference*, not the file. Two
contact rows now point at the same stored file. If one contact is later deleted and any file-cleanup runs
(or Django's `ImageField` delete semantics are added), the other contact's photo breaks. Same aliasing risk
applies to any future document registration for copied contacts.

**Recommended fix:** copy the underlying file (save a new `ContentFile`) rather than the reference, or
document that photos are intentionally shared and never hard-deleted.

**Fix shipped:** the target contact is now created first without a photo, then (if the source has one) the
raw bytes are read and `.save()`'d onto the target's own `photo` field via `ContentFile`, re-running
`contact_photo_upload_path` under the target's own contact ID — a genuinely separate file, not a shared
reference.

**Collateral bug found and fixed while verifying this:** the first verification attempt failed with
`SuspiciousFileOperation` — the F9 path change (`events/{event_id}-{slug}/...`) made every event-scoped path
noticeably longer, and `Event.featured_image`, `EventDay.event_images`, `EventContact.photo`,
`BudgetPayment.receipt`, and the three `document_hub` file fields were all still on Django's `ImageField`/
`FileField` default `max_length=100` — too short for the new path shape. All six were bumped to
`max_length=500` (migrations applied). Without this, **any real file upload** for an event with a
reasonably long slug would have started failing after the F9 change, not just contact-copy.

**Verified end-to-end:** created a real contact with an actual uploaded file, copied it via the fixed logic,
confirmed the target got its own distinct storage path, confirmed both files' byte content matched
(genuine duplication, not corruption), then deleted the source's file and confirmed the target's file
remained fully intact and readable — proving the two are truly independent.

---

## P2 — Edge-case robustness

### F7. `create_event` swallows the missing-portal case silently — ✅ FIXED
**Where:** [events/views.py:61-66](../apps/events/views.py#L61-L66).

```python
except ClientPortal.DoesNotExist:
    pass  # edge case: celebrant user has no portal configured yet
```

If the celebrant has no portal (e.g. a staff/admin user, or a client whose portal signal didn't fire), the
event is created with **no engagement and no warning**. The event exists in `getall_event` but is invisible
in the portal, and nothing tells the operator. Combined with F3, this is a second path into the "ghost
event" state.

**Recommended fix:** log a warning (or return a soft advisory in the response) when an event is created for
a user without a portal, rather than silently passing.

**Fix shipped:** the bare `pass` now logs a `logger.warning(...)` (with `event_id`, `event_slug`,
`celebrant_email`) via a module-level logger, matching the `logging.getLogger(__name__)` pattern already
used in `document_hub/tasks.py`/`meetings/tasks.py`/`notifications/tasks.py`. Response shape (still returns
plain `serializer.data` at `201`) was deliberately left unchanged to avoid a breaking contract change.

### F8. Portal auto-creation depends on `role == "client"` at every save — ✅ ACCEPTED AS INTENDED
**Where:** [portal/signals.py:7-10](../apps/portal/signals.py#L7-L10).

The `post_save` signal `get_or_create`s a portal only when `instance.role == "client"`. Consequences:
- A user created as `staff`/`admin` never gets a portal; if an event is later created for them, F7 fires.
- The signal runs on **every** save of a client user (idempotent but a redundant query each time).
- Role transitions (client → staff → client) leave portal state that no longer matches role expectations.

**Recommended fix:** consider creating the portal explicitly at registration time (where role is known and
intentional) rather than reactively on every save, and decide the policy for role changes.

**Decision (2026-07-12):** this is intended behavior, not a bug — the current signal-based,
`role == "client"`-gated auto-creation is the normal mode of operation. No fix planned, no code change made.

### F9. Slug uniqueness is never released and accumulates `-N` suffixes — ✅ COMPLETE
**Where:** [events/models.py:40-57](../apps/events/models.py#L40-L57).

Slugs are frozen on creation (correct, per the comment) and globally unique. Deleting an event does not
free its slug for reuse, and re-creating "the same" event yields `…-1`, `…-2`, etc. This is the exact
friction behind the duplicate-cleanup workflow. It also means a future soft-delete (F1 alternative) would
need to explicitly mangle/release the slug on soft-delete, or every recreation collides.

**Recommended fix:** acceptable as-is for now; note it as a constraint for any soft-delete design.

**What shipped, and what deliberately didn't:** the *reason* slugs had to stay frozen forever was that
storage paths embedded the slug directly (`core/utils.py`) — changing a slug after any file existed would
orphan the old file's path (see HEPHZIBAH_LUXE_AUDIT_AND_PLAN.md §4.3). That coupling is now removed: all
seven upload-path helpers (`event_cover_upload_path`, `event_image_upload_path`, `contact_photo_upload_path`,
`budget_receipt_upload_path`, `client_document_upload_path`, `invoice_upload_path`, `receipt_upload_path`)
now key their folder on the immutable `event.pk`, with the slug kept only as a cosmetic, human-readable
suffix (`events/{event_id}-{slug}/...`). No migration was needed — existing uploaded files keep resolving
at their old paths; only new uploads use the new shape.

**Explicitly rejected, by product decision:** automatic slug "reclaim" (renaming another event's slug back
to the clean version when the event holding it is deleted). This was designed and discussed in depth —
including a safe-window heuristic and a slug-history/redirect-table approach — but the owner does not want
any automatic slug mutation, ever. **Do not build this without an explicit new request.** Slugs still behave
exactly as before: frozen at creation, still accumulate `-N` on a title collision, never reclaimed.

### F10. `create_event` race on the portal's first active engagement — ✅ FIXED
**Where:** [events/views.py:84-99](../apps/events/views.py#L84-L99).

**Correction to the original finding:** this is not actually about `EventEngagement.event`'s `OneToOneField`
(that can't collide here — `create_event` always builds a brand-new `Event` row first, so it can't collide
with itself). The constraint that actually fires is `unique_active_engagement_per_portal` — the partial
unique index allowing only one `is_active=True` engagement per portal.

The bug is a classic check-then-act race: `if not portal.engagements.filter(is_active=True).exists():` is
checked, then acted on, as two separate steps. Two near-simultaneous `create_event` calls for the **same
celebrant** (double-click, a frontend retry, two staff members creating an event for the same client at
once) can both pass the check before either commits, then both attempt `.create()` — the second raises
`IntegrityError`. Worse: by that point the *second request's `Event` row is already committed* (it's an
earlier, separate write), so the request as a whole 500s while a real, engagement-less "ghost" event
silently persists — a race-triggered path into the same state F3/F7 already describe.

**Why the originally-suggested `get_or_create` fix (matching `activate_engagement`) doesn't actually work
here:** `get_or_create`'s automatic recovery re-queries using the *same lookup kwargs* it was given. In this
race, the two requests have different `event` values (`event_A` vs `event_B`), so the loser re-querying
`get(portal=portal, event=event_B)` still finds nothing (that row was never created — the constraint blocked
it) and re-raises the original `IntegrityError` anyway. `get_or_create` only helps when the exact same row
is processed twice, which isn't this code path's actual risk.

**Fix shipped:** wrap the `.create()` call in `try/except IntegrityError`, treating "lost the race" the same
way the code already treats "not the first event" — silently no-op (with an `INFO` log for visibility),
rather than crash. Verified by directly reproducing the race: created two events under a portal with zero
active engagements, confirmed the second `EventEngagement.objects.create(..., is_active=True)` raises
`IntegrityError` exactly as predicted, and confirmed the losing event ends up with no engagement (matching
F3's intended non-first-event state) instead of crashing the request.

---

## P3 — Consistency & polish

- **F11.** `last_updated_by` on `EventContact` is a free-text name string, not a user FK — unreliable for
  audit and breaks if a user is renamed. ([contacts/models.py:31](../apps/contacts/models.py#L31)) — ✅ FIXED.
  Changed to `models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)`,
  matching `Reminder.created_by`'s existing pattern. Views now pass `request.user` directly instead of a
  formatted name string (`create_contact`, `contact_detail` PATCH, `copy_contacts_from_day`). A human-readable
  `last_updated_by_display` `SerializerMethodField` was added to `EventContactSerializer`/
  `EventContactListSerializer` (mirroring the existing `category_display`/`preferred_method_display`
  pattern) so the API still returns a readable name — but computed fresh at read time from the linked
  account, so it can never go stale the way the old frozen string did. `last_updated_by` was also removed
  from `EventContactCreateSerializer`'s writable fields (it's server-set, never client-supplied).
  Migration required `RemoveField`+`AddField` rather than a plain `AlterField` — Postgres refused to cast
  existing free-text values (e.g. `"tobi ojulari"`) directly into the new integer FK column. Old values are
  intentionally not preserved, since they were never reliable account references to begin with. Verified
  end-to-end: existing rows cleared safely (no cast errors), assigning a real user and reading it back
  through both serializers renders the correct display name, reverted the test data afterward.
- **F12.** Delete endpoints return `200` with a `{"detail": ...}` body rather than `204 No Content`
  (minor REST-convention drift; the project's response conventions in CLAUDE.md §13 say 200 for delete, so
  this is intentional — noted for awareness).
- **F13.** `Notification.engagement` is `SET_NULL`, so notifications survive engagement deletion but lose
  all context (which event/client). Fine as a design choice; flag if notification history is ever audited.

---

## Status: all 13 findings closed (as of 2026-07-12)

**Closed: F1, F2, F3 (accepted, not a bug), F4, F5, F6, F7, F8 (accepted, not a bug), F9, F10, F11, F12, F13.**
Nothing remains open.

> F1/F2/F3/F4/F5/F6/F7/F9/F10/F11 were addressed across this conversation (impact-preview delete
> confirmation, staff-only deletes + independent `event_details_locked` toggle, the `activate_event`
> endpoint plus its new content-summary warning, a warning log for portal-less event creation, the
> `Document.object_id` CharField fix, decoupling storage paths from the slug, a race-safe fix for the
> portal's-first-engagement creation, the contact-photo-copy fix — which also surfaced and fixed a
> `max_length` regression on six file fields caused by the F9 path change — and converting
> `EventContact.last_updated_by` to a real user FK). F3 and F8 were reviewed and confirmed as intended
> behavior, not bugs — no code changed for either. F12/F13 were reviewed and confirmed as intentional
> design, no action needed.
