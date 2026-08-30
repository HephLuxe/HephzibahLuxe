# Hephzibah Luxe — Brevo Email Templates

The premium, standardized system every transactional email is built on. Each
of the 13 `NotificationType`s maps 1:1 to a real Brevo template (see
`apps/notifications/README.md`). This folder documents each template's HTML,
its merge fields, and how to wire it up.

**Start here → [`user_credentials.md`](./user_credentials.md)** — the base
layout every other template extends. All 13 templates are now fully built
(§5) on the same colours, button, and footer defined below.

---

## 1. How a template goes live (the workflow)

1. **Design** — build the HTML in Brevo (below is copy-paste ready).
   Brevo → *Campaigns → Templates → New template → Import / Code your own*.
2. **Set the subject inside Brevo** — the real subject line lives on the Brevo
   template, **not** in code. `Notification.subject` only stores the type label
   for the admin audit trail. Suggested subjects are in each template doc.
3. **Save & note the numeric template ID** Brevo assigns (visible in the
   template list / URL, e.g. `#12`).
4. **Wire the ID** into the matching env var — see the table below. These map
   to `config/settings.py` (`env.int(...)`) and `services.TEMPLATE_ID_MAP`.
5. **Send a test** from Brevo using the "Test parameters" JSON in each doc, so
   you preview with realistic merge data.

```env
# backend/.env  — one numeric Brevo template ID per type
BREVO_TEMPLATE_USER_CREDENTIALS=
BREVO_TEMPLATE_PASSWORD_RESET=
BREVO_TEMPLATE_NEW_REMINDER=
BREVO_TEMPLATE_PAYMENT_DUE=
BREVO_TEMPLATE_MEETING_PREP_DUE=
BREVO_TEMPLATE_PHASE_ADVANCED=
BREVO_TEMPLATE_EVENT_DETAILS_UPDATED=
BREVO_TEMPLATE_DOCUMENT_ADDED=
BREVO_TEMPLATE_INVOICE_ISSUED=
BREVO_TEMPLATE_RECEIPT_ISSUED=
BREVO_TEMPLATE_MILESTONE_PAID=
BREVO_TEMPLATE_INQUIRY_RECEIVED=
BREVO_TEMPLATE_INQUIRY_SUBMITTED_INTERNAL=
```

Merge fields are referenced in Brevo as `{{ params.FIELD }}` — the keys are
exactly the `context` dict each caller passes to `queue_notification()`.

---

## 2. Design tokens (use these everywhere — do not improvise)

| Token | Hex | Use |
|---|---|---|
| Ink | `#062025` | Header/footer bands, headings, primary button, key values |
| Canvas | `#F3F1EC` | Outer email background (the "matte" behind the card) |
| Card | `#FFFFFF` | The content card |
| Gold | `#B79A66` | Eyebrow labels, small accents |
| Gold soft | `#C9B48A` | Tagline on dark, secondary accent |
| Body | `#4A5456` | Paragraph text |
| Muted | `#7A8385` | Fine print, secondary notes |
| Label grey | `#8A9092` | Uppercase micro-labels inside cards |
| Hairline | `#ECEAE4` | Dividers, card borders |
| Subtle fill | `#F8F7F4` | Info/credential cards, quiet panels |
| Cream text | `#FBFAF7` | Text on the ink bands |
| Success | bg `#CDEADC` / text `#1E5B3B` | "Paid" pill |
| Pending | bg `#F4DED2` / text `#A85636` | "Pending / due" pill |
| Danger | bg `#F6DAD3` / text `#9E3B24` | Overdue / alerts |

**Type scale**

- Headings: `'Cormorant Garamond', 'Cormorant', Georgia, 'Times New Roman', serif`
  — H1 `36px/1.15`, section `24px`, signature `20px`.
- Body: `'Inter', -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif`
  — `16px/1.7` body, `14px` fine print.
- Eyebrow / micro-labels: `11–12px`, `600`, `letter-spacing:2px`, UPPERCASE.

**Geometry & spacing**

- Card width `600px`, `border-radius:16px`, `1px` hairline border.
- Buttons: **pill** (`border-radius:100px`), `17px 40px` padding, uppercase
  `letter-spacing:2px` label. Rounded corners degrade to square in Outlook —
  handled by the VML fallback in the button component.
- Inner content padding `48px` desktop → `24px` mobile (via the `.px` class).
- Section rhythm in multiples of 4/8px.

---

## 3. The building blocks (reused by every template)

### Base skeleton
`<head>` resets + fonts, a hidden **preheader**, a cream wrapper, the `600px`
white card with a dark **header band** (logo) at the top and a dark **footer
band** at the bottom. Copy the full skeleton from
[`user_credentials.md`](./user_credentials.md) — it is the canonical base; a new
template only swaps the middle "body rows".

### Logo
The header uses **`HEPHZIBAH LUXE LOGO.png`** (white logo → must sit on the dark
`#062025` band). Brevo can host it: *Templates → the image picker → Upload*,
then copy the URL it gives you and replace `LOGO_URL_HERE`. Keep `alt="Hephzibah
Luxe"` so the brand still reads if images are blocked. The gold tagline
*"Event Planning & Design Studio"* is live text, so the header never looks empty.

### CTA placeholders: `PORTAL_BASE_URL_HERE` and `SITE_BASE_URL_HERE`
Only two templates carry their own deep-link param
(`user_credentials`→`login_url`, `new_reminder`→`link_url`). The remaining
eleven have no per-record URL in their `context` dict, and they do not all
resolve the same way — the split is:

- **Eight point at a static portal route** — rather than a bare "sign in" link,
  each CTA points at the real section route (pulled straight from
  `apps/core/deeplinks.py`'s `TARGET_TYPES`, not guessed). Table below.
- **One points outside the portal entirely** — `inquiry_received` links to the
  public marketing site via `SITE_BASE_URL_HERE`, not `PORTAL_BASE_URL_HERE`.
- **Two carry no CTA at all** — `password_reset` and
  `inquiry_submitted_internal`. Neither has a placeholder to replace.

The eight portal routes (the last row is `new_reminder`'s fallback, which is
not one of the eight — it is listed here because that is where its CTA lands
when `link_url` comes through empty):

| Template | Route |
|---|---|
| `payment_due`, `milestone_paid` | `/portal/financials/client` |
| `invoice_issued` | `/portal/financials/client` |
| `receipt_issued` | `/portal/financials/client` |
| `meeting_prep_due` | `/portal/meetings` |
| `phase_advanced`, `event_details_updated` | `/portal/event-details` |
| `document_added` | `/portal/contracts/client` |
| `new_reminder` (fallback only, when `link_url` is empty) | `/portal` |

Replace `PORTAL_BASE_URL_HERE` (once per template) with your deployed
frontend root — the same value as `settings.FRONTEND_BASE_URL`. The path
suffix is already hardcoded correctly in each template; don't change it
unless the frontend route itself is renamed in `deeplinks.py`.

**`SITE_BASE_URL_HERE` — a second placeholder, for a different origin.** It
exists because of one recipient type: a lead. `inquiry_received` is sent to
someone who has just filled in the public contact form — no account, no
credentials, no `Event` row — so a `/portal/...` URL would drop them on a login
wall. Its "View our past projects" CTA therefore points at the **public
marketing site** (`SITE_BASE_URL_HERE/projects`), which is a different thing
from `settings.FRONTEND_BASE_URL` even in the deployments where the two happen
to share a hostname. The names are kept distinct precisely so a blind
find-and-replace of `PORTAL_BASE_URL_HERE` can't quietly start mailing leads a
login screen. Like the portal links, it is substituted by hand in Brevo rather
than passed as a param — nothing about the URL is per-record. One caveat the
portal placeholder does not have: the `/projects` suffix is **not** pinned by
`deeplinks.py` — the marketing site's routes live outside this repo — so verify
it against the live site rather than trusting it the way you can trust the
portal routes above.

**No-CTA templates need no placeholder, and that is deliberate in both cases.**
`password_reset` omits it because the user is already in the app typing the
code; `inquiry_submitted_internal` omits it because the only useful destination
for staff is the Django admin, which lives on the *backend* origin — naming
that origin would mean a second base-URL env var beside `FRONTEND_BASE_URL`,
the exact drift hazard `config/settings.py` argues against. Adding a button to
either is a regression, not an improvement.

### Bulletproof pill button
Rounded corners need a VML fallback or Outlook renders a square. Always ship
both halves:

```html
<!--[if mso]>
<v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word"
  href="{{ params.LINK }}" style="height:54px;v-text-anchor:middle;width:280px;"
  arcsize="50%" strokecolor="#062025" fillcolor="#062025">
  <w:anchorlock/>
  <center style="color:#FBFAF7;font-family:Arial,sans-serif;font-size:13px;letter-spacing:2px;font-weight:bold;">BUTTON LABEL</center>
</v:roundrect>
<![endif]-->
<!--[if !mso]><!-- -->
<a href="{{ params.LINK }}" style="display:inline-block;background-color:#062025;color:#FBFAF7;font-family:'Inter',Arial,sans-serif;font-size:13px;font-weight:600;letter-spacing:2px;text-transform:uppercase;text-decoration:none;padding:17px 40px;border-radius:100px;">Button label</a>
<!--<![endif]-->
```

### Status pill
```html
<span style="display:inline-block;background-color:#CDEADC;color:#1E5B3B;font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:.5px;padding:5px 14px;border-radius:100px;">Paid</span>
```

### Detail / summary card
Label-over-value rows on the `#F8F7F4` panel with `#ECEAE4` hairline dividers —
see the "credential card" in `user_credentials.md`. Reuse it for invoice
amounts, payment summaries, event details, etc.

---

## 4. Why these choices (things beyond the basics that are handled)

- **Preheader text** — the hidden one-liner that shows in the inbox next to the
  subject. Set per template; padded with zero-width chars so client text doesn't
  leak in after it.
- **Outlook (Word engine)** — table-based layout, VML round buttons, `mso`
  PPI fix, Georgia/Arial fallbacks since web fonts don't load there.
- **Dark-mode friendliness** — `color-scheme: light` is declared so clients
  don't force-invert and muddy the gold/teal. The dark bands stay dark by design.
- **Images-off resilience** — brand reads from live text, not just the logo PNG;
  every image has real `alt`.
- **Accessibility** — body ≥16px, AA contrast (ink on white, cream on ink),
  semantic headings, meaningful link text.
- **Transactional footer** — physical address + contact on every send (good
  practice and keeps deliverability clean). These are transactional, not
  marketing, so no unsubscribe link is required.
- **Mobile** — single column, fluid to `100%`, padding tightens to `24px`,
  tap targets stay large.

---

## 5. The 13 templates — all built

| Doc | `template_name` | Env var | Merge fields |
|---|---|---|---|
| [user_credentials.md](./user_credentials.md) | `user_credentials` | `BREVO_TEMPLATE_USER_CREDENTIALS` | `display_name`, `user_email`, `temporary_password`, `login_url` |
| [password_reset.md](./password_reset.md) | `password_reset` | `BREVO_TEMPLATE_PASSWORD_RESET` | `user_first_name`, `code`, `expires_in_minutes` |
| [new_reminder.md](./new_reminder.md) | `new_reminder` | `BREVO_TEMPLATE_NEW_REMINDER` | `title`, `description`, `priority_display`, `due_date`, `link_url`, `link_label` |
| [payment_due.md](./payment_due.md) | `payment_due` | `BREVO_TEMPLATE_PAYMENT_DUE` | `label`, `amount`, `due_date`, `event_title` |
| [meeting_prep_due.md](./meeting_prep_due.md) | `meeting_prep_due` | `BREVO_TEMPLATE_MEETING_PREP_DUE` | `meeting_title`, `meeting_date`, `incomplete_count` |
| [phase_advanced.md](./phase_advanced.md) | `phase_advanced` | `BREVO_TEMPLATE_PHASE_ADVANCED` | `phase_display`, `event_title` |
| [event_details_updated.md](./event_details_updated.md) | `event_details_updated` | `BREVO_TEMPLATE_EVENT_DETAILS_UPDATED` | `event_title`, `what` |
| [document_added.md](./document_added.md) | `document_added` | `BREVO_TEMPLATE_DOCUMENT_ADDED` | `document_title`, `category_display`, `event_title` |
| [invoice_issued.md](./invoice_issued.md) | `invoice_issued` | `BREVO_TEMPLATE_INVOICE_ISSUED` | `invoice_number`, `amount`, `due_on`, `event_title` |
| [receipt_issued.md](./receipt_issued.md) | `receipt_issued` | `BREVO_TEMPLATE_RECEIPT_ISSUED` | `receipt_number`, `amount`, `payment_for`, `event_title` |
| [milestone_paid.md](./milestone_paid.md) | `milestone_paid` | `BREVO_TEMPLATE_MILESTONE_PAID` | `label`, `amount`, `paid_on`, `event_title` |
| [inquiry_received.md](./inquiry_received.md) | `inquiry_received` | `BREVO_TEMPLATE_INQUIRY_RECEIVED` | `first_name` |
| [inquiry_submitted_internal.md](./inquiry_submitted_internal.md) | `inquiry_submitted_internal` | `BREVO_TEMPLATE_INQUIRY_SUBMITTED_INTERNAL` | `recipient_name`, `first_name`, `last_name`, `email`, `phone_number`, `contact_mode`, `event_type`, `desired_location`, `preferred_start_date`, `preferred_end_date`, `budget`, `details`, `submitted_at`, `inquiry_id` |

> Amounts arrive as **strings** (`Decimal` is serialised to `str`), dates as
> **ISO strings** (`_serialise_context` in `notifications/services.py`). Format
> them for display in the copy, e.g. write `₦{{ params.amount }}` yourself — the
> value is the bare number.
