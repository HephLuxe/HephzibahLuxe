# Template: `inquiry_submitted_internal`

The internal lead alert. Sent to staff the moment a public inquiry is submitted
(`inquiries.services.create_inquiry`) — one separate send per flagged staff
member, never one email with several recipients.

| | |
|---|---|
| `template_name` | `inquiry_submitted_internal` |
| Env var | `BREVO_TEMPLATE_INQUIRY_SUBMITTED_INTERNAL` |
| Fired by | `inquiries.services.create_inquiry` (immediate, one send per flagged staff member) |
| Merge fields | `recipient_name`, `first_name`, `last_name`, `email`, `phone_number`, `contact_mode`, `event_type`, `desired_location`, `preferred_start_date`, `preferred_end_date`, `budget`, `details`, `submitted_at`, `inquiry_id` |

**Suggested subject:** `New inquiry from {{ params.first_name }} {{ params.last_name }}`
**Preheader:** `A new lead just came in through the website.`

Reuses the shared base layout — see [`README.md`](./README.md) for tokens and
[`user_credentials.md`](./user_credentials.md) for the annotated skeleton.

---

## Test parameters

```json
{
  "recipient_name": "Tobi",
  "first_name": "Chidinma",
  "last_name": "Okonkwo",
  "email": "chidinma.okonkwo@gmail.com",
  "phone_number": "+2348034512907",
  "contact_mode": "Phone Number",
  "event_type": "Wedding",
  "desired_location": "Victoria Island, Lagos",
  "preferred_start_date": "2027-02-13",
  "preferred_end_date": "2027-02-14",
  "budget": "45000000.00",
  "details": "We are planning a two-day traditional and white wedding for about 400 guests. Looking for full planning and decor, and we would like a first call before the end of the month.",
  "submitted_at": "2026-08-18T09:42:11.480312+00:00",
  "inquiry_id": "8f3c1e6a-2d47-4b91-a0f5-9c7ee31b2d54"
}
```

Fourteen keys — the exact `context` dict from
[`../INQUIRY_IMPLEMENTATION_PLAN.md`](../INQUIRY_IMPLEMENTATION_PLAN.md) §2.2.
Test with a `budget` of `"Not specified"` as well as a number; see the notes.

## Before you send

1. Replace `LOGO_URL_HERE` with the hosted logo URL (Brevo image picker).
2. Do **not** add a CTA button or a `PORTAL_BASE_URL_HERE` link — this template
   deliberately has neither, and the reason is in the notes below. There is no
   URL placeholder in this file at all.
3. Paste the subject, save, set
   `BREVO_TEMPLATE_INQUIRY_SUBMITTED_INTERNAL=<id>`.

---

## Full HTML

```html
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
  <title>New inquiry received</title>
  <!--[if mso]>
  <noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript>
  <![endif]-->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    body,table,td,a{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;}
    table,td{mso-table-lspace:0pt;mso-table-rspace:0pt;}
    img{-ms-interpolation-mode:bicubic;border:0;height:auto;line-height:100%;outline:none;text-decoration:none;}
    body{margin:0;padding:0;width:100%!important;height:100%!important;background-color:#F3F1EC;}
    a{text-decoration:none;}
    @media only screen and (max-width:620px){
      .container{width:100%!important;border-radius:0!important;}
      .px{padding-left:24px!important;padding-right:24px!important;}
      .h1{font-size:30px!important;}
    }
    @media only screen and (max-width:480px){
      .col2{display:block!important;width:100%!important;border-right:none!important;border-bottom:1px solid #ECEAE4;}
      .col2:last-child{border-bottom:none!important;}
    }
  </style>
</head>
<body style="margin:0;padding:0;background-color:#F3F1EC;">

  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#F3F1EC;opacity:0;">
    A new lead just came in through the website.
    &#8199;&#65279;&#8199;&#65279;&#8199;&#65279;&#8199;&#65279;&#8199;&#65279;&#8199;&#65279;&#8199;&#65279;
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F3F1EC;">
    <tr>
      <td align="center" style="padding:32px 16px;">

        <table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;background-color:#FFFFFF;border-radius:16px;overflow:hidden;border:1px solid #ECEAE4;">

          <!-- header band -->
          <tr>
            <td align="center" style="background-color:#062025;padding:38px 40px 30px;">
              <img src="LOGO_URL_HERE" width="150" alt="Hephzibah Luxe" style="display:block;width:150px;max-width:150px;height:auto;margin:0 auto 12px;">
              <div style="font-family:'Cormorant Garamond',Georgia,serif;font-size:13px;letter-spacing:3px;text-transform:uppercase;color:#C9B48A;">Event Planning &amp; Design Studio</div>
            </td>
          </tr>

          <!-- eyebrow + heading -->
          <tr>
            <td class="px" style="padding:46px 48px 0;">
              <div style="font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#B79A66;">Internal — new lead</div>
              <h1 class="h1" style="margin:12px 0 0;font-family:'Cormorant Garamond',Georgia,serif;font-size:36px;line-height:1.15;font-weight:600;color:#062025;">New inquiry received</h1>
            </td>
          </tr>

          <!-- copy -->
          <tr>
            <td class="px" style="padding:22px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              Hi {{ params.recipient_name }},
            </td>
          </tr>
          <tr>
            <td class="px" style="padding:16px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              A new inquiry has just been submitted through the website. Everything the lead sent is below — the client has already been told we will reach out within 2 business days.
            </td>
          </tr>

          <!-- detail card: contact block -->
          <tr>
            <td class="px" style="padding:28px 48px 0;">
              <div style="font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#B79A66;padding-bottom:10px;">Contact</div>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F8F7F4;border:1px solid #ECEAE4;border-radius:12px;">
                <tr>
                  <td style="padding:22px 28px 16px;font-family:'Inter',Arial,sans-serif;">
                    <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Name</div>
                    <div style="margin-top:6px;font-size:16px;color:#062025;font-weight:500;">{{ params.first_name }} {{ params.last_name }}</div>
                  </td>
                </tr>
                <tr><td style="padding:0 28px;"><div style="height:1px;background-color:#ECEAE4;line-height:1px;font-size:0;">&nbsp;</div></td></tr>
                <tr>
                  <td style="padding:0;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td class="col2" width="50%" valign="top" style="padding:16px 28px;font-family:'Inter',Arial,sans-serif;border-right:1px solid #ECEAE4;">
                          <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Email</div>
                          <div style="margin-top:6px;font-size:15px;color:#062025;font-weight:500;word-break:break-all;">{{ params.email }}</div>
                        </td>
                        <td class="col2" width="50%" valign="top" style="padding:16px 28px;font-family:'Inter',Arial,sans-serif;">
                          <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Phone</div>
                          <div style="margin-top:6px;font-size:15px;color:#062025;font-weight:500;">{{ params.phone_number }}</div>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr><td style="padding:0 28px;"><div style="height:1px;background-color:#ECEAE4;line-height:1px;font-size:0;">&nbsp;</div></td></tr>
                <tr>
                  <td style="padding:16px 28px 22px;font-family:'Inter',Arial,sans-serif;">
                    <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Preferred contact mode</div>
                    <div style="margin-top:6px;font-size:15px;color:#062025;font-weight:500;">{{ params.contact_mode }}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- detail card: event block -->
          <tr>
            <td class="px" style="padding:26px 48px 0;">
              <div style="font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#B79A66;padding-bottom:10px;">Event</div>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F8F7F4;border:1px solid #ECEAE4;border-radius:12px;">
                <tr>
                  <td style="padding:0;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td class="col2" width="50%" valign="top" style="padding:22px 28px 16px;font-family:'Inter',Arial,sans-serif;border-right:1px solid #ECEAE4;">
                          <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Event type</div>
                          <div style="margin-top:6px;font-size:15px;color:#062025;font-weight:500;">{{ params.event_type }}</div>
                        </td>
                        <td class="col2" width="50%" valign="top" style="padding:22px 28px 16px;font-family:'Inter',Arial,sans-serif;">
                          <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Desired location</div>
                          <div style="margin-top:6px;font-size:15px;color:#062025;font-weight:500;">{{ params.desired_location }}</div>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr><td style="padding:0 28px;"><div style="height:1px;background-color:#ECEAE4;line-height:1px;font-size:0;">&nbsp;</div></td></tr>
                <tr>
                  <td style="padding:0;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td class="col2" width="50%" valign="top" style="padding:16px 28px 22px;font-family:'Inter',Arial,sans-serif;border-right:1px solid #ECEAE4;">
                          <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Preferred start</div>
                          <div style="margin-top:6px;font-size:15px;color:#062025;font-weight:500;">{{ params.preferred_start_date }}</div>
                        </td>
                        <td class="col2" width="50%" valign="top" style="padding:16px 28px 22px;font-family:'Inter',Arial,sans-serif;">
                          <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Preferred end</div>
                          <div style="margin-top:6px;font-size:15px;color:#062025;font-weight:500;">{{ params.preferred_end_date }}</div>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- budget -->
          <tr>
            <td class="px" style="padding:26px 48px 0;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F8F7F4;border:1px solid #ECEAE4;border-radius:12px;">
                <tr>
                  <td style="padding:22px 28px;font-family:'Inter',Arial,sans-serif;">
                    <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Budget (&#8358;)</div>
                    <div style="margin-top:6px;font-family:'Cormorant Garamond',Georgia,serif;font-size:26px;color:#062025;font-weight:600;">{{ params.budget }}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- details (free text) -->
          <tr>
            <td class="px" style="padding:26px 48px 0;">
              <div style="font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#B79A66;padding-bottom:10px;">Their message</div>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F8F7F4;border:1px solid #ECEAE4;border-radius:12px;">
                <tr>
                  <td style="padding:22px 28px;font-family:'Inter',Arial,sans-serif;font-size:15px;line-height:1.7;color:#4A5456;white-space:pre-wrap;">{{ params.details }}</td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- submitted meta -->
          <tr>
            <td class="px" style="padding:24px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:13px;line-height:1.8;color:#7A8385;">
              Submitted {{ params.submitted_at }}<br>
              Inquiry ID <span style="font-family:'Courier New',Consolas,monospace;color:#4A5456;">{{ params.inquiry_id }}</span>
            </td>
          </tr>

          <!-- how to action it -->
          <tr>
            <td class="px" style="padding:20px 48px 40px;font-family:'Inter',Arial,sans-serif;font-size:13px;line-height:1.8;color:#7A8385;">
              No link is included by design. Open the Django admin, go to <strong style="color:#4A5456;">Inquiries</strong>, and search the ID above to update the lead's status.
            </td>
          </tr>

          <!-- footer band -->
          <tr>
            <td style="background-color:#062025;padding:38px 48px;">
              <div style="font-family:'Cormorant Garamond',Georgia,serif;font-size:22px;letter-spacing:3px;color:#FBFAF7;text-align:center;">HEPHZIBAH&nbsp;LUXE</div>
              <div style="margin-top:8px;font-family:'Inter',Arial,sans-serif;font-size:12px;line-height:1.8;color:#9FB0B2;text-align:center;">
                Crafting refined, beautiful and meaningful celebrations.<br>
                29 Adeniran Ogunsanya, Surulere, Lagos, Nigeria &nbsp;&bull;&nbsp; 0802&nbsp;320&nbsp;3870
              </div>
              <div style="margin-top:18px;font-family:'Inter',Arial,sans-serif;font-size:11px;color:#5E7175;text-align:center;">
                © 2026 Hephzibah Luxe. All rights reserved.
              </div>
            </td>
          </tr>

        </table>

      </td>
    </tr>
  </table>
</body>
</html>
```

---

## Notes specific to this email

- **`budget` is a STRING, not a number.** `_serialise_context` in
  `notifications/services.py` coerces `Decimal` to `str`, so what lands in the
  template is `"45000000.00"` — bare digits, no symbol, no grouping. Adding the
  ₦ is the template's job, exactly as `milestone_paid` does with
  `&#8358;{{ params.amount }}`. (Note the precedent's limit: `milestone_paid`
  adds the symbol only — it does **not** insert thousands separators, and
  neither can this template, because Brevo's template language has no
  number-formatting filter and the backend sends the raw `str(Decimal)`. If
  grouped digits matter to staff, the format has to change on the backend, not
  here.)
- **When the lead picks "not sure yet", `budget` arrives as the literal string
  `"Not specified"`** — passed that way deliberately so this template needs no
  conditional. That is exactly why the ₦ is **not** inline with the value here:
  `&#8358;{{ params.budget }}` would render `₦Not specified` on every lead who
  skipped the question. **Recommendation, and what the HTML above does: put the
  currency in the micro-label — `Budget (₦)` — and render `{{ params.budget }}`
  bare.** It reads correctly in both cases (`Budget (₦) / 45000000.00` and
  `Budget (₦) / Not specified`), needs no `{% if %}`, and keeps the one-branch
  guarantee the backend was written to provide. A conditional would work too,
  but it moves a rule that is currently enforced and tested in Python into an
  untested Brevo template.
- **Dates and timestamps are ISO strings.** `preferred_start_date` and
  `preferred_end_date` come from `.isoformat()` on a `DateField`
  (`"2027-02-13"`), and `submitted_at` is the row's `created_at` as a full ISO
  datetime (`"2026-08-18T09:42:11.480312+00:00"`, UTC). They are printed
  as-is — this is an internal email, and an unambiguous machine format is more
  useful to staff than a prettified one that hides the timezone.
- **`inquiry_id` is a UUID string** (`_serialise_value` coerces `uuid.UUID` to
  `str`). It is printed in monospace precisely so staff can copy it into the
  admin search box and land on the exact row — that is the substitute for the
  link this email does not have.
- **No CTA button, and no `PORTAL_BASE_URL_HERE` — deliberately**, following
  `password_reset`'s precedent of omitting the CTA and saying why. The useful
  destination for staff is the **Django admin**, not the client portal, and the
  admin lives on the *backend* origin. Putting that URL in the email would mean
  a second base-URL env var sitting next to `FRONTEND_BASE_URL` — and
  `config/settings.py` is explicit about why that is a trap: two env vars naming
  one deployment is a drift hazard, "point one at staging, forget the other",
  and the link quietly sends people to the wrong environment. A staging-pointed
  admin link arriving in a production inbox is precisely that failure. The
  printed `inquiry_id` is the deliberate trade.
- **One send per flagged staff member.** `create_inquiry` loops over
  `User.objects.filter(receives_inquiry_alerts=True, is_active=True,
  is_staff=True)` and calls `queue_notification()` once per person, so
  `recipient_name` is that individual staff member's first name, not the lead's.
  N staff means N `Notification` rows with independent status and retry.
- **No client signature block.** The "With warm regards, The Hephzibah Luxe
  Team" row that closes every client-facing template is dropped here — this
  email is from the system to the team, and signing it back to the team reads as
  a copy-paste mistake.
- `details` is optional free text and may contain newlines. `white-space:pre-wrap`
  preserves them where supported (Apple Mail, Gmail web, most webmail); Outlook's
  Word engine ignores it and collapses the message to a paragraph, which is
  acceptable for an internal alert. If the lead left it blank the panel simply
  renders empty — no conditional, matching the rest of this template.
- **The nullable fields can arrive as `null`.** `event_type`, `contact_mode`,
  `preferred_start_date`, `preferred_end_date` and `details` are all
  `null=True` on `InquiryForm`, and `_serialise_value` passes `None` straight
  through, so those rows render **blank** rather than showing a placeholder.
  `budget` is the one field with an explicit `"Not specified"` substitution.
  This is also why the suggested subject uses only `first_name` /
  `last_name` — both required — instead of `event_type`, which would leave a
  dangling subject line on a lead who skipped it.
- **PII lives in this email by design** (phone, budget, the lead's message) and
  it is safe: `NotificationHistorySerializer` omits `context` entirely, and the
  history query never matches a staff-addressed row to a client account. See
  `../INQUIRY_IMPLEMENTATION_PLAN.md` §2.2.
