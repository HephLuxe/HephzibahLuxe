# Hephzibah Luxe — Model Relationship Map

The shape of the schema, not a field reference. Per-model field documentation
lives in each app's `README.md`; the API shapes live in `docs/API_CONTRACT*.md`.

---

## 1. The spine

Everything that belongs to a client hangs off one chain:
**`User → ClientPortal → EventEngagement → Event`.** `EventEngagement` is the
load-bearing link — it is where planning *state* lives, and almost every
per-client record FKs to it rather than to the portal or the event.

```
User (accounts)
  │
  ├──1:1──► ClientPortal (portal)          permanent identity; almost no state
  │            ├──N:N──► TeamMember (via PortalTeamAssignment)
  │            └──1:N──► EventEngagement (portal)
  │                         │   bridges portal ↔ event; every event gets one.
  │                         │   At most ONE is_active=True per portal, enforced by a
  │                         │   partial UniqueConstraint. Owns current_phase, both
  │                         │   locks, phase_details, reference_segment, and the
  │                         │   event-details email debounce columns.
  │                         │
  │                         ├──1:1──► Event (events)                 ◄─ same Event as below
  │                         ├──1:N──► Meeting (meetings)
  │                         │            ├──1:N──► MeetingPrepItem
  │                         │            │            └──1:N──► PrepItemField
  │                         │            │                         ├──1:1──► PrepItemResponse
  │                         │            │                         └──1:N──► PrepItemFileUpload
  │                         │            └──1:1──► MeetingNotes
  │                         ├──1:N──► Conversation (conversations)
  │                         │            (ConversationReply — planned, not implemented)
  │                         ├──1:N──► Reminder (reminders)
  │                         │            └──GenericFK──► target (any deep-linkable row;
  │                         │                 registry in apps/core/deeplinks.py)
  │                         ├──1:N──► Document (documents — generic upload registry, GenericFK
  │                         │            to source; object_id is a CharField, so int- and
  │                         │            UUID-PK sources both work)
  │                         ├──1:N──► Notification (notifications)
  │                         ├──1:N──► ClientDocument (document_hub)
  │                         ├──1:1──► PaymentSchedule (document_hub)
  │                         │            └──1:N──► PaymentMilestone
  │                         ├──1:N──► Invoice (document_hub)
  │                         └──1:N──► Receipt (document_hub)
  │
  └──1:N──► Event (events)                              [User is `celebrant`, SET_NULL]
                 ├──1:N──► EventDay
                 ├──1:1──► EventEngagement (portal)         [same object as above]
                 ├──1:1──► EventBudget (budgets)
                 │            ├──1:N──► BudgetCategory
                 │            └──1:N──► BudgetPayment
                 └──1:N──► EventContact (contacts)          [also FK'd to EventDay — both
                                                             FKs required, so every contact
                                                             is pinned to one day]
```

**Reading the engagement fan-out.** `Meeting.engagement` and
`Conversation.engagement` are nullable (`null=True`) for historical rows;
everything else in that block requires one. `Notification.engagement` is nullable
on purpose — a staff-facing alert has no engagement, which is what lets the
inquiry app reuse the notification pipeline (see §3).

---

## 2. Standalone and system models

Not part of the spine. Nothing FKs *to* any of these.

```
InquiryForm (inquiries)      pre-relationship lead capture — see §3
PasswordResetToken (accounts) ──FK──► User
PortalSettings (portal)      singleton (pk=1), admin-only, no API.
                             Auto-lock config + notification debounce + the
                             business contact email/WhatsApp shown on the portal
PortalDefaults (document_hub) portal-wide document/welcome defaults
ReferenceCounter (document_hub) per-event-type counter behind HL-PSW006-INV001
NotificationTypeSettings      per-notification-type on/off switches
ScheduledTaskSettings         per-cron-task on/off switches
ServiceHealthState            Brevo circuit-breaker state
```

---

## 3. `InquiryForm` — where it sits, precisely

**It is outside the spine, but it is no longer FK-free.** This is the point most
often stated wrongly.

```
InquiryForm (inquiries)
  ├─ NOT linked to Event, ClientPortal or EventEngagement
  ├─ carries its own first_name / last_name / email / phone_number, because the
  │  submitter is an anonymous prospect with no account
  ├─ inherits UUIDTimestampedModel  → UUID pk, created_at, updated_at
  └─ inherits AttributedModel       → created_by / last_updated_by, both FK ► User
                                       (SET_NULL, editable=False)
```

Three consequences worth holding on to:

- **`created_by` is permanently NULL, and that is correct** — the submit route is
  public and unauthenticated, so no staff member creates a lead.
- **`last_updated_by` / `updated_at` *is* the status attribution.** `status` is
  the only mutable field on the model (the read serializer is entirely read-only
  and the sole write route is the status PATCH), so the generic pair already means
  "who moved this lead, and when". That is why there is no bespoke
  `status_updated_by` here the way `EventEngagement` carries `phase_updated_by`: a
  portal has many mutable fields and must single one out; an inquiry has exactly
  one. **If a second mutable field lands** (`assigned_to` — see
  `docs/INQUIRY_V2_Upgrades.md` A2), that reasoning expires.
- **`event_type` shares one vocabulary with `Event.EVENT_TYPE`** by pointing at it
  directly (`choices=Event.EVENT_TYPE`), not by re-declaring the list. It is a
  shared *vocabulary*, not a relation — there is no FK. Adding an event type is a
  single edit on `events.Event`.

Conversion (lead → client) is not built. When it lands it adds two nullable FKs
(`converted_user`, `converted_event`) and is the point at which an inquiry finally
touches the spine.

---

## 4. The cross-cutting relation the diagram above omits

Almost every model in the project also carries **two FKs back to `User`**, from
the abstract `core.AttributedModel`:

```
AttributedModel (abstract, apps/core/models.py)
  ├─ created_by       ──FK──► User   SET_NULL, null=True, editable=False
  └─ last_updated_by  ──FK──► User   SET_NULL, null=True, editable=False
```

Inherited by: `ClientPortal`, `TeamMember`, `PortalTeamAssignment`,
`EventEngagement`, `Event`, `EventDay`, `Meeting`, `MeetingPrepItem`,
`PrepItemField`, `MeetingNotes`, `Conversation`, `Reminder`, `EventContact`,
`ClientDocument`, `PaymentSchedule`, `PaymentMilestone`, `Invoice`, `Receipt`,
`EventBudget`, `BudgetCategory`, `BudgetPayment`, and **`InquiryForm`**.

**Not** inherited by: `Document` (carries its own `uploaded_by`),
`PrepItemResponse`, `PrepItemFileUpload`, `Notification`, `PasswordResetToken`,
and the system/settings models in §2.

Four things follow from this that are easy to get wrong:

1. **`SET_NULL` everywhere, deliberately.** Deleting a staff account never
   cascades away the records they touched — the row survives with the field nulled
   and the display name falls back to `""`. This is also why users are
   *deactivated*, never deleted.
2. **`editable=False`.** These are system-set from `request.user` via
   `core.utils.save_with_attribution` / `stamp_attribution`. Admin saves bypass
   those helpers, so attribution covers the API surface only.
3. **Last-writer-wins.** It is attribution, not history.
4. **Reverse accessors are namespaced** `%(app_label)s_%(class)s_created` /
   `_updated`, or two dozen models inheriting one abstract base would collide.

### Attribution FKs that are *not* from `AttributedModel`

| Field | On | Why it exists separately |
|---|---|---|
| `EventEngagement.phase_updated_by` / `phase_updated_at` | portal | tracks **specifically** who moved the planning phase. `last_updated_by` moves on any save — a lock toggle, a rename — so it can't back the "Last updated by …" line on the Planning Stage. The pre-generalisation ancestor of `AttributedModel` |
| `Document.uploaded_by` | documents | predates `AttributedModel`; the model is a registry, not an edited record |
| `Notification.recipient_user` | notifications | not attribution at all — it is *who the email is for*. Nullable, which is what lets the inquiry app email a stranger with no account |
| `User.deactivated_by` | accounts | a self-FK: which staff member offboarded this user, part of the reversible-deactivation audit trio |
| `Event.celebrant` | events | ownership, not attribution — the client whose event this is |

---

## 5. Identifier conventions

- **URL path parameters are UUIDs, project-wide** — including `EventDay`,
  `Meeting`, `MeetingPrepItem`, `PrepItemField`, `Conversation`, `TeamMember`,
  `ClientPortal`, `EventEngagement` and `InquiryForm`. A non-UUID segment fails to
  match the route and 404s from the URLconf rather than raising inside a view.
- **`Event` is the exception: it is addressed by `slug`.** `/event/<slug>/…` is
  the shape everywhere, and `PortalOverviewSerializer.active_event` returns the
  slug string for exactly that reason.
- **Two GenericFKs exist** (`Document.source`, `Reminder.target`), and both use a
  `CharField` `object_id` rather than an integer or UUID column — so a target with
  an int PK and one with a UUID PK both work through the same registry.
