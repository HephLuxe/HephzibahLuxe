# Conversations/Questions & Clarifications App

The `conversations` app facilitates communication thread recording, status tracking, and archiving within client portals. It is designed to catalog key communications that occur over external channels (like WhatsApp or email) to keep planning phases structured.

---

## Models

### `Conversation`
Represents an individual communication thread.
* **engagement**: ForeignKey to `EventEngagement`. Associates the conversation with a specific planning workspace engagement.
* **phase**: CharField using `PlanningPhase` choices (`connect`, `align`, `curate`, `envision`, `orchestrate`, `deliver`).
* **conversation_with**: CharField naming the client or team contact involved in the conversation.
* **contact_method**: CharField using `ContactMethod` choices (`whatsapp`, `phone`, `email`).
* **title**: TextField representing the headline of the thread.
* **body**: TextField detailing the full notes or transcript of the message thread.
* **tags**: JSONField list of categorization tags (validated against `ConversationTag`).
* **links**: JSONField list of deep-link pills shown on the card — see below.

`ContactMethod`: `whatsapp`, `phone`, `email`.

`ConversationTag` — doubles as the "Filter by" checkbox vocabulary **and** the
deep-link pill vocabulary (e.g. a conversation tagged `EVENT_DETAILS` links
back to the Event Details page) — one list serves both purposes rather than
two that could drift apart: `aso_ebi`, `catering`, `creative_design`,
`decor_design`, `entertainment`, `event_details`, `budget`, `souvenir`, `venue`.

---

## Business Logic (`services.py`)

* **`validate_tags(tags: list) -> list`**: Checks that every submitted tag is
  a valid `ConversationTag` value (the list above).
* **`validate_links(links: list, engagement) -> list`**: See "Deep-link pills"
  below.

## Deep-link pills

Each entry in `links` is either **targeted** —
`{"target_type": "event_contact", "target_id": "<uuid>"}`, with an optional
`"label"` override — or **free-text**, for links with no object behind them:
`{"label": "View Event Details", "url": "/portal/event-details"}`.

A targeted entry shares the exact same registry `reminders` uses
(`apps/core/deeplinks.py` / `resolve_target`) — see `apps/reminders/README.md`
for the full mechanism. On read, a targeted entry comes back resolved to
`{label, url, target_type, target_id}` with `url` derived fresh each time; an
entry whose target has since been deleted is **dropped from the response**
rather than served as a pill that 404s. A target that doesn't exist, is
malformed, or belongs to **another client's engagement** is rejected at
create/update time with `code=validation_error` — same cross-tenant guarantee
reminders get.

---

## Serializers

* **`ConversationCreateSerializer`**: Handles incoming creation data, validating fields like `conversation_with` and `contact_method`.
* **`ConversationListSerializer`**: A lightweight serializer for listing records, including human-readable `phase_display` and `contact_method_display`.
* **`ConversationDetailSerializer`**: Provides the full message payload including the `body` field.
* **`ConversationUpdateSerializer`**: Handles partial updates for active threads.

---

## List behavior worth remembering

The list view defaults to the **first 4** conversations per query, not the
full set — `?limit=all` is the explicit "load everything" escape hatch. This
matches the Figma's per-phase accordion (a short preview per phase, expandable
on demand) rather than shipping every thread on first load.

---

## Tips & gotchas

- **Conversations hang off `EventEngagement`, not `ClientPortal`.** A client with
  two events has two separate Q&A threads — pass `engagement_id` on create to
  pre-stage a future event's thread, or omit it for the active engagement.
- **`ConversationReply` was planned but never implemented.** Clients can read a
  thread but cannot reply through the API today; `conversation_with` +
  `contact_method` record who the conversation happened *with* and how, as a
  staff-authored log rather than a two-way inbox. Don't assume replies exist.
- **`tags` and `phase` are fixed vocabularies**, not free text — fetch them from
  `GET /conversations/tags/` and `/conversations/phases/` rather than hardcoding
  the lists in the frontend. Tags do double duty: the "Filter by" checkboxes and
  the deep-link pills on each card.
- Writes are **staff-only** (create, update, delete); clients read.
- Attribution applies — `created_by_display` / `last_updated_by_display` tell you
  which staff member logged or last edited the thread.
