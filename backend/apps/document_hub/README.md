# document_hub

The "HL Client Document Hub" page: Service Agreement, Quotation, Welcome &
Service Information PDFs, Payment Overview (milestone tracker), Invoices, and
Receipts. All under `/api/v1/`.

Deliberately a **separate app from `apps.documents`**, which is a generic-FK
store for internal media assets (event covers, contact photos, prep uploads)
produced as a side effect of other actions. Everything here is a client-facing,
staff-authored record with its own lifecycle.

## Models
| Model | Purpose |
|---|---|
| `ClientDocument` | Service Agreement / Quotation / Welcome Booklet / FAQ / Other. `reference_code` + `signed_on` only meaningful for SVC_AGREEMENT/QUOTATION (the two signable categories). |
| `PaymentSchedule` | One per engagement. `total_investment` + computed `paid_to_date` (sum of `amount_paid`) / `remaining_balance` / `next_payment_milestone` — backs the Payment Overview tiles. |
| `PaymentMilestone` | Deposit / Phase 2 / Final Payment rows under a schedule. Carries a `percentage` (its share of `total_investment`) which is the source of truth; `amount` is derived from it. See **Contract payment split** below. |
| `Invoice` | Numbered invoice rows (`invoice_number`, issued/due dates, amount, status). `milestone` links it to what it bills for — paying it is what moves the schedule. |
| `Receipt` | Numbered receipt rows (`receipt_number`, paid_on, payment_for, amount). |
| `PortalDefaults` | **Singleton** config: 3 template file slots (Service Agreement / Welcome Booklet / FAQ) + a default `welcome_message`. Only the **FAQ** is auto-seeded onto a new engagement; the other two slots are kept but attached per client. See **Auto-seeded portal defaults** below. |
| `ReferenceCounter` | Race-safe counters behind the auto-generated reference codes. Read-only in the admin. |

## Auto-seeded portal defaults

The **FAQ** and the **welcome message** are genuinely identical for every
client, so staff configure them **once** on the singleton `PortalDefaults`
record (Django admin, or `GET`/`PATCH /document-hub/defaults/`) and each new
engagement/portal gets a copy automatically.

The **Service Agreement**, **Quotation** and **Welcome Booklet** are *not*
boilerplate — a service agreement is a per-client legal document and the welcome
booklet differs per client — so they are **never auto-seeded**, even though the
first two still have upload slots on `PortalDefaults`. Attach them per client
with `POST /document-hub/documents/`.

**What happens when:**
- **A new `EventEngagement` is created** (any path — `events.create_event` or
  `portal.activate_engagement`), its Document Hub is auto-populated with a copy
  of the configured **FAQ** template as a `ClientDocument`
  (`services.seed_engagement_documents`, fired by a `post_save` signal in
  `signals.py`). The file **bytes are cloned** onto the engagement's own document
  (independent copy, not a shared blob — same pattern as
  `contacts.copy_contacts_from_day`). The Service Agreement, Quotation, and
  Welcome Booklet are **not** seeded — they're per-client, so staff attach them
  via `POST /document-hub/documents/`.
- **A new `ClientPortal` is created**, its `welcome_message` is set from the
  default (`apply_default_welcome_message`) — only if the portal has none yet.

**Rules & edge cases:**
- **Seeded per engagement.** A multi-event client gets its own copies on each
  event's hub. Re-activating a past event never re-seeds (the signal fires on
  create only).
- **Idempotent / skip-safe.** A template slot with no file configured is
  skipped; a category the engagement already has is skipped (so a staff-deleted
  doc is never silently re-added).
- The seeded FAQ carries **no** `reference_code` — only the signable categories
  do. A Service Agreement gets its `HL-…-C001` the moment staff create it via
  `create_document` (the auto-generation `pre_save` signal fires on every save
  with an engagement, not just the API path), leaving only
  `is_signed`/`signed_on` for staff to set once the client actually signs.
- **New clients only.** Existing engagements/portals are not backfilled, and
  editing the defaults never rewrites already-seeded client documents.

**Editing:** a specific client's seeded document is replaced/edited via the
existing `PATCH /document-hub/documents/<id>/`; a client's welcome message via
`PATCH /portal/`. The `/document-hub/defaults/` endpoint manages only the
org-wide templates for *future* clients.

## Contract payment split

A `PaymentSchedule` is split into phases by **percentage of `total_investment`**,
and the percentage is the **source of truth** — the money `amount` on each
milestone is *derived* (`percentage/100 × total_investment`), not stored
independently. The default contract structure is **30% Deposit / 40% Phase 2 /
30% Final Payment**.

**What happens when:**
- **Creating a schedule** (`POST .../payment-schedule/`) auto-generates the
  default 30/40/30 milestones **and one linked invoice per milestone** — staff
  only send `total_investment`. See *Invoices drive the schedule* below.
- **Changing `total_investment`** (`PATCH .../payment-schedule/<uuid>/`) re-splits
  the percentage-based milestones over the new total automatically, so amounts
  stay consistent. Edit the total once; the phases follow.
- **Reconfiguring a client's split** happens in the **Django admin**: edit the
  milestone `percentage`s inline (validated to sum to exactly 100), or use the
  **"Reset to default 30/40/30 split"** action. `amount` is read-only in the
  inline and recomputed on save. Frontend/API callers never set the amounts.
- The **last milestone absorbs any rounding remainder**, so amounts always sum
  *exactly* to `total_investment` (e.g. a 33/33/34 split of 100.00 → 33.00 /
  33.00 / 34.00).

**Invariant:** a schedule's percentage-based milestones must sum to 100. **Ad-hoc,
one-off milestones** added via `POST .../payment-schedule/<uuid>/milestones/`
carry **no** `percentage` — they're fixed-amount extras that sit *outside* the
sum-to-100 rule and are never touched by the re-split.

An ad-hoc milestone is extra money the client owes **Hephzibah Luxe**, beyond
the signed `total_investment` — e.g. a late-addition handling fee for
onboarding a vendor the client sourced themselves. It is **never** a vendor's
own cost passing through — vendor-by-vendor spend is tracked entirely
separately in `apps.budgets` (`BudgetPayment`), which has nothing to do with
`PaymentSchedule`/`PaymentMilestone`.

Logic lives in `services.py`: `DEFAULT_PAYMENT_SPLIT`, `validate_split()`,
`generate_milestones()`, `recompute_milestone_amounts()`. **Frontend:** each
milestone in the `GET /document-hub/` aggregate includes `percentage`,
`amount`, `amount_paid`, `balance`, `status`, `due_date`, and `paid_on`.

## Invoices drive the schedule

**One direction, no double entry: pay the invoice, and the Payment Overview
follows.** Nothing else moves it.

`Invoice.milestone` is the link. It did not exist before — `Invoice` had a
single FK (engagement), `paid_to_date` summed milestones, and nothing read an
invoice's status. So staff issued invoices mirroring the milestones by hand,
flipped one to paid, and the tiles did not move; the only way to shift them was
to *also* mark the milestone paid. The same fact, entered twice, with nothing
keeping the two entries honest.

| Field | Written by | Meaning |
|---|---|---|
| `Invoice.status` | **staff** | the one thing you set |
| `PaymentMilestone.amount_paid` | derived | sum of that milestone's **paid** invoices |
| `PaymentMilestone.status` | derived | `pending` / `part_paid` / `paid`, from `amount_paid` vs `amount` |
| `PaymentMilestone.paid_on` | derived | `issued_on` of the last invoice that settled it |
| `PaymentSchedule.paid_to_date` | derived | sum of every milestone's `amount_paid` |

`amount_paid`, `status` and `paid_on` are **read-only on the API and in the
admin inline**. A PATCH that sets them is ignored rather than rejected — they
are recomputed from the invoices on the next sync, so accepting a written value
would only mean it disappeared later without explanation.

**Part payments are first class.** `PART_PAID` exists because money arrives in
amounts the plan did not predict — ₦1,500,000 against a ₦2,800,000 phase. A
paid/pending boolean has to round that to one of two lies; `amount_paid` +
`balance` do not. `next_payment_due_amount` is the **balance**, not the
milestone's face value, so a part-paid milestone is never billed twice.

**Reversals converge.** `sync_milestone_from_invoices` recomputes from the whole
invoice set rather than adding a delta, so unpaying an invoice, deleting one, or
editing its amount all land on the right number. Deleting the last paid invoice
takes its money back off the milestone.

**Milestones with no invoice still work.** `PATCH .../milestones/<id>/mark-paid/`
writes an unlinked milestone directly — that covers ad-hoc milestones and every
row predating the link. When the milestone *does* have invoices, the same
endpoint marks **those** paid and lets the sync settle the milestone, so the two
records can never disagree.

**Receipts drive nothing.** A `Receipt` is the client-facing proof of a payment,
not the payment record. Invoices are the single source of truth for the
schedule.

**Existing data:** invoices raised before this feature have `milestone = NULL`
and drive nothing. Pair them up with

```
python manage.py link_invoices_to_milestones            # dry run, prints the pairing
python manage.py link_invoices_to_milestones --apply
```

It matches by amount within an engagement and only ever touches invoices with no
milestone, so it is safe to re-run. It is a command and not a data migration
precisely because the pairing is a **guess** — a 30/40/30 split has two
milestones at the same amount, and that wants an eyeball before `--apply`.

Logic lives in `services.py`: `derive_milestone_status()`,
`sync_milestone_from_invoices()`, `sync_invoice_milestone()`,
`issue_invoices_for_schedule()`, `propagate_due_date()`.

### Reference codes are auto-generated & read-only
`reference_code` / `invoice_number` / `receipt_number` follow
**`HL-<segment>-<TYPE><NNN>`** (e.g. `HL-PSW006-C001`, `HL-PSW006-Q001`,
`HL-PSW006-INV001`, `HL-PSW006-R001`) and are **system-generated** — staff never
type them (they're read-only in the API and admin).

- **`<segment>`** (e.g. `PSW006`) identifies the **engagement** and encodes its
  event. Assigned once on first need and frozen. It's `<II><CODE><NNN>`:
  - **`<II>`** — two initials from the event's names. Wedding: bride + groom
    (`Priscilla & Samuel` → `PS`). Birthday: `honoree_name`. Corporate / Social /
    Others: `event_name`. Two-or-more words → first letter of the first two words
    (`Tola Obi` → `TO`); a single word → its first two letters (`Acme` → `AC`).
  - **`<CODE>`** — the event-type letter: Wedding `W`, Birthday `B`,
    Corporate `C`, Social Events `S`, Others `O`.
  - **`<NNN>`** — how many of that event type the business has ever done (a
    **global per-event-type** counter): the 6th wedding → `006`. Two different
    birthdays with the same initials stay distinct via the count (`…AAB002…`,
    `…AAB003…`). Stored on `EventEngagement.reference_segment`.
- **`<TYPE>`** ∈ `C` (Service Agreement), `Q` (Quotation), `INV` (Invoice),
  `R` (Receipt). Only the four coded record types get a code (Welcome Booklet /
  FAQ / Other don't).
- **`<NNN>`** (the trailing one, e.g. in `INV001`) restarts at `001`
  **per engagement, per type**.

Generation lives in `services.next_reference_code` /
`services.assign_engagement_segment` (backed by the race-safe `ReferenceCounter`)
and is applied by **`pre_save` signals**, so every path — API, Django admin, the
auto-seed, the shell — gets a code. An engagement with `reference_segment = NULL`
is assigned one lazily the first time a code is generated for it.
The regex validator (`^HL-[A-Za-z0-9]+-[A-Za-z]+\d+$`) still guards the format.

### File storage
Cloudflare R2 (S3-compatible) support is wired up in `config/settings.py` via
`django-storages`, gated behind `USE_R2_STORAGE` (default `False`) — see the
"Media storage" block in settings.py. **Not active yet**: real R2 API
credentials (`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`) haven't been created,
so `.env` currently has the endpoint/bucket filled in but the flag off, and
everything still writes to local `MEDIA_ROOT` exactly as before. Once real
keys are dropped in and `USE_R2_STORAGE=True` is set, uploads switch to R2
with **signed, expiring URLs** (`AWS_QUERYSTRING_AUTH=True`, 1-hour default)
— appropriate for client-facing documents (contracts, invoices, receipts)
that shouldn't be reachable by anyone who guesses or shares a URL. No code
changes needed in this app when that switch flips — `FileField`/`ImageField`
read the active storage backend automatically.

Upload paths are keyed by portal **UUID only** (`core.utils.client_document_upload_path`
etc.) — deliberately not by event slug, unlike some older upload paths in
this codebase, to avoid the slug-regeneration-orphans-files bug documented in
the audit/plan (§4.3, now fixed — slugs are frozen after creation).

## Client notifications

Four immediate, per-type-gated notifications (see `apps/notifications/README.md`
for the shared `NotificationTypeSettings` on/off mechanism — each of these has
its **own** admin toggle, independent of the others and of every other
notification type in the system):

| Trigger | `template_name` |
|---|---|
| Staff creates a `ClientDocument` (`POST /document-hub/documents/`) | `document_added` |
| Staff creates an `Invoice` (`POST /document-hub/invoices/`) | `invoice_issued` |
| Staff creates a `Receipt` (`POST /document-hub/receipts/`) | `receipt_issued` |
| A `PaymentMilestone` is marked paid (`services.mark_milestone_paid`) | `milestone_paid` |

**Deliberately excluded: `seed_engagement_documents`.** The boilerplate FAQ
cloned onto every new engagement happens the instant the engagement is created
— often before the client has even completed their first login. Notifying "a
new document was added" at that moment would just be noise stacked on top of the
separate welcome/credentials email. Only documents a staff member adds
*afterward* (the Service Agreement, a client-specific quotation, etc.) notify.

## Read pattern

Clients only ever read via the `GET /document-hub/` aggregate — there is no
client-facing single-item GET, since the aggregate already returns every file
URL needed for View/Download. Writes (create/edit/delete documents, payment
schedule, milestones, invoices, receipts) are staff-only.

Every document key in that response is a **list**, `service_agreements` and
`quotations` included. Those two were singular (`.first()`) until a revised
quotation went up and the hub showed only the newer one — the write path has
always allowed several per engagement and numbers them `C001`/`C002`,
`Q001`/`Q002`, so the read path was the half that was wrong. Each list is
ordered newest-first (`Meta.ordering = ["order", "-created_at"]`); a frontend
that only wants the current quotation takes `quotations[0]`.

## Tests
`python manage.py test document_hub` — empty-state shape, staff create →
client read, permission denial, reference-code validation, payment-schedule
tiles + next-payment-due derivation, mark-paid.

---

## Tips & gotchas

- **`svc_agreement`, not `contract`.** The Service Agreement category value was
  renamed (the label was always "Service Agreement"; the internal name lagged).
  Reference codes still use the **`C`** type letter, so existing
  `HL-…-C001` codes are unaffected.
- **Signing fields are hidden where they're meaningless.** `is_signed`,
  `signed_on` and `reference_code` are only serialised for the two
  `SIGNABLE_CATEGORIES` (Service Agreement / Quotation). On an FAQ or Welcome
  Booklet they're dropped — `is_signed: false` there reads as "this FAQ is
  unsigned" rather than "signing doesn't apply". Same `to_representation`
  approach `EventSerializer` uses for wedding-only fields.
- **Only the FAQ auto-seeds.** A new engagement gets a copy of the configured
  FAQ template and nothing else. The Service Agreement, Quotation and Welcome
  Booklet are per-client — add them with `POST /document-hub/documents/`
  (`category: "svc_agreement" | "quotation" | "welcome_booklet"`), which is also
  where their reference code and the `document_added` email come from.
- **One document endpoint, five categories.** `POST /document-hub/documents/`
  requires `portal_id`, `category`, `title` and `file`; `engagement_id` is
  optional (defaults to the portal's *active* engagement, so you can pre-stage a
  future event's paperwork). `reference_code` is read-only — a client-supplied
  value is ignored, not rejected.
- **Never bulk-`update()` an invoice's status.** `Invoice.objects.filter(...)
  .update(status="paid")` writes the column and fires nothing — no signal, no
  service — so the linked milestones stay pending and the Payment Overview does
  not move. That is the exact bug the milestone link exists to fix. Save row by
  row and call `services.sync_invoice_milestone()`, as `InvoiceAdmin.mark_as_paid`
  does.
- **There's no per-category uniqueness.** Two Service Agreements on one
  engagement is allowed and yields `C001`, `C002`, and `GET /document-hub/`
  returns both in `service_agreements` — same for `quotations`. Only the
  *seeding* path skips a category the engagement already has.
- **Deleting a payment schedule is gated twice.** `?confirm=true` is required
  when milestones exist, and a schedule with any **paid** milestone is refused
  outright — that's a payment record, not a draft. Clear or unmark those first.
- **`ReferenceCounter` is read-only in the admin** (no add, no change) on
  purpose: editing a counter changes what the *next* code will be, and lowering
  one produces duplicates that collide with codes already issued to clients.
  Useful to *look* at — `eventtype:W` tells you how many weddings have been
  numbered.
