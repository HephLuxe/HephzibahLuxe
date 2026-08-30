# Template: `inquiry_received`

The acknowledgement a lead gets the moment they submit the public inquiry form
(`inquiries.services.create_inquiry`) — static copy plus their first name, and
nothing else.

| | |
|---|---|
| `template_name` | `inquiry_received` |
| Env var | `BREVO_TEMPLATE_INQUIRY_RECEIVED` |
| Fired by | `inquiries.services.create_inquiry` (immediate) |
| Merge fields | `first_name` |

**Suggested subject:** `We have received your inquiry, {{ params.first_name }}`
**Preheader:** `Thank you for reaching out — our team will be in touch shortly.`

Reuses the shared base layout — see [`README.md`](./README.md) for tokens and
[`user_credentials.md`](./user_credentials.md) for the annotated skeleton.

---

## Test parameters

```json
{
  "first_name": "Chidinma"
}
```

One key. That is the whole context dict — see
[`../INQUIRY_IMPLEMENTATION_PLAN.md`](../INQUIRY_IMPLEMENTATION_PLAN.md) §2.1.
Any `{{ params.X }}` you add beyond `first_name` renders **empty** on a real
send, because the backend will never pass it.

## Before you send

1. Replace `LOGO_URL_HERE` with the hosted logo URL (Brevo image picker).
2. Replace `SITE_BASE_URL_HERE` (twice — the VML fallback and the `<a>`) with
   the **public marketing site** root, e.g. `https://hephzibahluxe.com`. This is
   deliberately **not** `PORTAL_BASE_URL_HERE`: the recipient is a lead with no
   account and no portal, so a `/portal/...` link would land them on a login
   wall. In practice that is `https://hephzibahluxe.com`, the origin the form
   itself is served from — the portal lives on `portal.hephzibahluxe.com`.
   **Confirm the path suffix.** Unlike the portal routes, `/projects` is not
   pinned by `apps/core/deeplinks.py` — nothing in this repo defines the
   marketing site's routes — so check the live site and change the suffix in
   both halves of the button if the past-work page sits somewhere else.
3. Paste the subject, save, set `BREVO_TEMPLATE_INQUIRY_RECEIVED=<id>`.

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
  <title>We have received your inquiry</title>
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
  </style>
</head>
<body style="margin:0;padding:0;background-color:#F3F1EC;">

  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#F3F1EC;opacity:0;">
    Thank you for reaching out — our team will be in touch shortly.
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
              <div style="font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#B79A66;">Thank you</div>
              <h1 class="h1" style="margin:12px 0 0;font-family:'Cormorant Garamond',Georgia,serif;font-size:36px;line-height:1.15;font-weight:600;color:#062025;">We have received your inquiry!</h1>
            </td>
          </tr>

          <!-- copy -->
          <tr>
            <td class="px" style="padding:22px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              Hi {{ params.first_name }},
            </td>
          </tr>
          <tr>
            <td class="px" style="padding:16px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              Thank you for reaching out to Hephzibah Luxe. Your inquiry has landed safely with our team and is already being reviewed.
            </td>
          </tr>
          <tr>
            <td class="px" style="padding:16px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              One of our planners will reach out to you <strong style="color:#062025;">within 2 business days</strong> to talk through what you have in mind. In the meantime, we would love for you to see some of the celebrations we have brought to life.
            </td>
          </tr>

          <!-- CTA -->
          <tr>
            <td class="px" align="center" style="padding:32px 48px 4px;">
              <!--[if mso]>
              <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="SITE_BASE_URL_HERE/projects" style="height:54px;v-text-anchor:middle;width:280px;" arcsize="50%" strokecolor="#062025" fillcolor="#062025">
                <w:anchorlock/>
                <center style="color:#FBFAF7;font-family:Arial,sans-serif;font-size:13px;letter-spacing:2px;font-weight:bold;">VIEW OUR PAST PROJECTS</center>
              </v:roundrect>
              <![endif]-->
              <!--[if !mso]><!-- -->
              <a href="SITE_BASE_URL_HERE/projects" style="display:inline-block;background-color:#062025;color:#FBFAF7;font-family:'Inter',Arial,sans-serif;font-size:13px;font-weight:600;letter-spacing:2px;text-transform:uppercase;text-decoration:none;padding:17px 40px;border-radius:100px;">View our past projects</a>
              <!--<![endif]-->
            </td>
          </tr>

          <!-- signature -->
          <tr>
            <td class="px" style="padding:34px 48px 40px;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              With warm regards,<br>
              <span style="font-family:'Cormorant Garamond',Georgia,serif;font-size:20px;color:#062025;">The Hephzibah Luxe Team</span>
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

- **`first_name` is the entire context — and that is a tested invariant, not an
  oversight.** This email carries none of what the lead typed into the form —
  no `event_type`, no `preferred_start_date` / `preferred_end_date`, no
  `desired_location`, no `budget`, no `details`. The one-key context exists so
  that any future change which leaks a lead's own submitted data into this
  email breaks a single, obvious assertion in the test suite. If you are here
  because you want to "helpfully" echo the submission back, change the test
  first and understand why it was written.
- **The recipient's own address is never echoed back either.** The
  *"a confirmation email has been sent to …"* reassurance belongs on the
  frontend success page, where the user has just typed the address and wants to
  see it confirmed. Repeating it inside the message that already arrived in that
  inbox adds nothing, and it would mean the backend passing a second param for
  no reason.
- **`SITE_BASE_URL_HERE`, not `PORTAL_BASE_URL_HERE` — read this before you
  "fix" it.** Every other template in this folder links into the client portal,
  because every other recipient has an account. A lead does not. They have no
  portal, no credentials, and no `Event` row yet, so the house
  `PORTAL_BASE_URL_HERE` placeholder (which resolves to
  `settings.FRONTEND_BASE_URL`) is the wrong value here. This is the first
  template in the set that points at the **public marketing site**, and the
  distinct placeholder name is what stops a future reader from assuming it was a
  typo. See `README.md` §3 for both placeholders side by side.
- **The URL is hardcoded in the Brevo template, not passed as a param.** There
  is nothing per-lead about it — every acknowledgement points at the same public
  page — which is the same reason `milestone_paid` and friends use a placeholder
  rather than shipping a link through the `context` dict.
- The subject uses `{{ params.first_name }}` too. That is still the same single
  param, so it does not widen the context; drop it to a plain
  `We have received your inquiry` if you prefer a non-personalised subject.
- No conditional logic anywhere in this template. `first_name` is a required,
  non-blank field on `InquiryForm`, so there is no empty-greeting case to guard.
