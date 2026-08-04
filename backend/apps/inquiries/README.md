# Inquiries App

The `inquiries` app captures **prospective-client enquiries** — the "get in
touch / plan my event" form a lead fills in before they become a client with a
portal. It is deliberately separate from the client-facing portal apps: an
inquiry is a lead record the Hephzibah Luxe team triages, not something tied to
an `EventEngagement`. The app is **admin-managed only** — it has a model and a
Django-admin registration, but no serializers, endpoints, or portal wiring.

---

## Models

### `InquiryForm`
One submitted enquiry.

* **first_name** / **last_name**: the lead's name.
* **email** / **phone_number**: contact details.
* **contact_mode**: preferred channel — `Email` or `Phone Number` (optional).
* **event_type**: `Birthday`, `Wedding`, `Corporate`, `Social Events`, or
  `Others` (optional). Mirrors the `Event.event_type` vocabulary so an accepted
  lead maps cleanly onto a real event later.
* **preferred_start_date** / **preferred_end_date**: the desired event window.
* **desired_location**: free-text location/venue preference.
* **budget**: optional `Decimal` budget indication.
* **details**: free-text notes about the enquiry.

**Date-range integrity is enforced at the database level.** A
`CheckConstraint` (`valid_preferred_date_range`) guarantees
`preferred_end_date >= preferred_start_date`, so an inverted window can never be
stored — regardless of how the row is created (admin, shell, or a future
public endpoint).

`__str__` renders as `"<first> <last> - <event_type or 'Event Inquiry'> @ <location>"`
for readable rows in admin lists.

---

## Admin

`InquiryForm` is registered in the Django admin as the triage surface for the
team: list display of the key lead fields, filters by `event_type` /
`contact_mode`, and search across name, email, phone, and location. This is the
only interface to the model today — there is no client/API path into it.

---

## Not implemented (by design, for now)

There are no serializers, views, or URL routes, and the app is **not** mounted
in `config/urls.py`. If a public "submit an enquiry" endpoint is added later,
it would live here as a serializer + a single unauthenticated create view; the
model and its date constraint are already ready for it.

---

## Tips & gotchas

- **`InquiryForm` is standalone** — no FK to `User`, `Event`, or a portal. It's
  pre-relationship lead capture: the submitter is an anonymous prospect, which is
  why the model carries its own `first_name`/`last_name`/`email`/`phone_number`
  rather than pointing at an account.
- **There are no API views yet** (`views.py` is a stub). Inquiries are currently
  read and worked in the Django admin. If you wire up a public submission
  endpoint, it must be **unauthenticated and rate-limited** (see
  `apps/core/ratelimit.py` for the pattern used by the auth endpoints) — it's an
  open write path and an obvious spam target.
- **Attribution deliberately does not apply.** There's no acting user to stamp,
  so `InquiryForm` is the one write model outside `core.AttributedModel`.
- Its `EVENT_TYPE` choices mirror `events.Event.EVENT_TYPE`. If you add an event
  type, add it in **both** places — and give it a reference-code letter in
  `document_hub.services.EVENT_TYPE_CODES`, or its engagements fall back to a
  blank event-type letter in their segment.
