# Template: `event_details_updated`

Debounced email sent after edits to an event or event day settle
(`events.services.schedule_event_details_notification` — see
`apps/events/README.md` for the debounce mechanism).

| | |
|---|---|
| `template_name` | `event_details_updated` |
| Env var | `BREVO_TEMPLATE_EVENT_DETAILS_UPDATED` |
| Fired by | `events.services.schedule_event_details_notification` (**debounced**, default 15 min after the last edit) |
| Merge fields | `event_title`, `what` |

**Suggested subject:** `Your event details have been updated`
**Preheader:** `Something on your event details page has changed.`

Reuses the shared base layout — see [`README.md`](./README.md).

---

## Test parameters

```json
{
  "event_title": "Adaeze & Michael's Wedding",
  "what": "Details for White Wedding Reception"
}
```

`what` is a short section label, not a full diff — callers pass values like
`"Event details"` (`apps/events/views.py`, editing the event itself) or
`"Details for <event day title>"` (editing an event day). Design copy around
it reading as a section name, not a sentence.

## Before you send

1. Replace `LOGO_URL_HERE` with the hosted logo URL.
2. Replace `PORTAL_BASE_URL_HERE` with your deployed frontend root — the CTA
   points at `/portal/event-details`.
3. Paste the subject, save, set `BREVO_TEMPLATE_EVENT_DETAILS_UPDATED=<id>`.

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
  <title>Event details updated</title>
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
    Something on your event details page has changed.
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
              <div style="font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#B79A66;">Event update</div>
              <h1 class="h1" style="margin:12px 0 0;font-family:'Cormorant Garamond',Georgia,serif;font-size:36px;line-height:1.15;font-weight:600;color:#062025;">Your event details have been updated</h1>
            </td>
          </tr>

          <!-- copy -->
          <tr>
            <td class="px" style="padding:22px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              Our team has made an update to <strong>{{ params.event_title }}</strong>. Here's what changed:
            </td>
          </tr>

          <!-- detail card -->
          <tr>
            <td class="px" style="padding:24px 48px 0;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F8F7F4;border:1px solid #ECEAE4;border-radius:12px;">
                <tr>
                  <td style="padding:22px 28px;font-family:'Inter',Arial,sans-serif;">
                    <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">What changed</div>
                    <div style="margin-top:6px;font-size:16px;color:#062025;font-weight:500;">{{ params.what }}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- CTA -->
          <tr>
            <td class="px" align="center" style="padding:32px 48px 4px;">
              <!--[if mso]>
              <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="PORTAL_BASE_URL_HERE/portal/event-details" style="height:54px;v-text-anchor:middle;width:280px;" arcsize="50%" strokecolor="#062025" fillcolor="#062025">
                <w:anchorlock/>
                <center style="color:#FBFAF7;font-family:Arial,sans-serif;font-size:13px;letter-spacing:2px;font-weight:bold;">VIEW EVENT DETAILS</center>
              </v:roundrect>
              <![endif]-->
              <!--[if !mso]><!-- -->
              <a href="PORTAL_BASE_URL_HERE/portal/event-details" style="display:inline-block;background-color:#062025;color:#FBFAF7;font-family:'Inter',Arial,sans-serif;font-size:13px;font-weight:600;letter-spacing:2px;text-transform:uppercase;text-decoration:none;padding:17px 40px;border-radius:100px;">View event details</a>
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

- Because sends are **debounced** (a burst of staff edits within the window
  collapses into one email describing only the *last* change), the copy
  deliberately doesn't claim to list every change — `what` only ever holds the
  final one. Don't add "and more" language; it isn't accurate to what the
  backend tracks.
- No amounts/dates here — just one detail row, kept as a single-column card
  matching the credential card's pattern in `user_credentials.md`.
