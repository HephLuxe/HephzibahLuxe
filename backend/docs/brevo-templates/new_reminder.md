# Template: `new_reminder`

Sent immediately when a staff member creates a client-facing reminder
(`reminders.services.create_reminder`).

| | |
|---|---|
| `template_name` | `new_reminder` |
| Env var | `BREVO_TEMPLATE_NEW_REMINDER` |
| Fired by | `reminders.services.create_reminder` (immediate) |
| Merge fields | `title`, `description`, `priority_display`, `due_date`, `link_url`, `link_label` |

**Suggested subject (merge-tag subject — Brevo supports `{{ params.x }}` in the
subject field itself):** `New reminder: {{ params.title }}`
**Preheader:** `A new reminder has been added to your Hephzibah Luxe portal.`

Reuses the shared base layout — see [`README.md`](./README.md).

---

## Test parameters

```json
{
  "title": "Upload your inspiration board",
  "description": "Please upload at least 5 reference images so our design team can finalise your moodboard ahead of the next session.",
  "priority_display": "High Priority",
  "due_date": "2026-07-22",
  "link_url": "https://portal.hephzibahluxe.com/portal/meetings?meetingId=8f21&prepItemId=44",
  "link_label": "Complete preparation"
}
```

Also test with `link_url` **empty** (`""`) — a reminder with no deep-linkable
target (`apps/core/deeplinks.py`: `resolve_target` returns nothing to link) —
to confirm the fallback button still renders sensibly.

## Before you send

1. Replace `LOGO_URL_HERE` with the hosted logo URL.
2. Replace `PORTAL_BASE_URL_HERE` with your deployed frontend root (the same
   value as `settings.FRONTEND_BASE_URL`, e.g. `https://portal.hephzibahluxe.com`)
   — used only as the **fallback** CTA target when `link_url` is empty.
3. Paste the subject above, save, set `BREVO_TEMPLATE_NEW_REMINDER=<id>`.

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
  <title>New reminder</title>
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
    A new reminder has been added to your Hephzibah Luxe portal.
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
              <div style="font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#B79A66;">New reminder</div>
              <h1 class="h1" style="margin:12px 0 0;font-family:'Cormorant Garamond',Georgia,serif;font-size:36px;line-height:1.15;font-weight:600;color:#062025;">{{ params.title }}</h1>
            </td>
          </tr>

          <!-- copy -->
          <tr>
            <td class="px" style="padding:22px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              {{ params.description }}
            </td>
          </tr>

          <!-- detail card: priority | due date -->
          <tr>
            <td class="px" style="padding:28px 48px 0;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F8F7F4;border:1px solid #ECEAE4;border-radius:12px;">
                <tr>
                  <td class="col2" width="50%" valign="top" style="padding:22px 28px;font-family:'Inter',Arial,sans-serif;border-right:1px solid #ECEAE4;">
                    <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Priority</div>
                    <div style="margin-top:8px;">
                      {% if params.priority_display == "High Priority" %}
                      <span style="display:inline-block;background-color:#F6DAD3;color:#9E3B24;font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:.5px;padding:5px 14px;border-radius:100px;">{{ params.priority_display }}</span>
                      {% elif params.priority_display == "Medium Priority" %}
                      <span style="display:inline-block;background-color:#F4DED2;color:#A85636;font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:.5px;padding:5px 14px;border-radius:100px;">{{ params.priority_display }}</span>
                      {% else %}
                      <span style="display:inline-block;background-color:#ECEAE4;color:#4A5456;font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:.5px;padding:5px 14px;border-radius:100px;">{{ params.priority_display }}</span>
                      {% endif %}
                    </div>
                  </td>
                  <td class="col2" width="50%" valign="top" style="padding:22px 28px;font-family:'Inter',Arial,sans-serif;">
                    <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Due date</div>
                    <div style="margin-top:8px;font-size:16px;color:#062025;font-weight:500;">{{ params.due_date }}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- CTA: the reminder's own deep link, or a portal fallback -->
          <tr>
            <td class="px" align="center" style="padding:32px 48px 4px;">
              {% if params.link_url %}
              <!--[if mso]>
              <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="{{ params.link_url }}" style="height:54px;v-text-anchor:middle;width:280px;" arcsize="50%" strokecolor="#062025" fillcolor="#062025">
                <w:anchorlock/>
                <center style="color:#FBFAF7;font-family:Arial,sans-serif;font-size:13px;letter-spacing:2px;font-weight:bold;">{{ params.link_label }}</center>
              </v:roundrect>
              <![endif]-->
              <!--[if !mso]><!-- -->
              <a href="{{ params.link_url }}" style="display:inline-block;background-color:#062025;color:#FBFAF7;font-family:'Inter',Arial,sans-serif;font-size:13px;font-weight:600;letter-spacing:2px;text-transform:uppercase;text-decoration:none;padding:17px 40px;border-radius:100px;">{{ params.link_label }}</a>
              <!--<![endif]-->
              {% else %}
              <!--[if mso]>
              <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="PORTAL_BASE_URL_HERE/portal" style="height:54px;v-text-anchor:middle;width:280px;" arcsize="50%" strokecolor="#062025" fillcolor="#062025">
                <w:anchorlock/>
                <center style="color:#FBFAF7;font-family:Arial,sans-serif;font-size:13px;letter-spacing:2px;font-weight:bold;">OPEN YOUR PORTAL</center>
              </v:roundrect>
              <![endif]-->
              <!--[if !mso]><!-- -->
              <a href="PORTAL_BASE_URL_HERE/portal" style="display:inline-block;background-color:#062025;color:#FBFAF7;font-family:'Inter',Arial,sans-serif;font-size:13px;font-weight:600;letter-spacing:2px;text-transform:uppercase;text-decoration:none;padding:17px 40px;border-radius:100px;">Open your portal</a>
              <!--<![endif]-->
              {% endif %}
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

- **`{% if params.link_url %}`** — Brevo's template language supports `{% if
  %}/{% elif %}/{% else %}/{% endif %}` blocks in the "Code your own" / HTML
  editor. `reminders.services.resolve_target` can leave a reminder with no
  linkable target, in which case `link_url` arrives empty — the fallback
  branch sends the reader to the portal generally rather than showing a dead
  button. Send a test with both an empty and populated `link_url` before going
  live.
- **Priority pill colours** are matched to the exact three values Django's
  `get_priority_display()` produces (`reminders.models.ReminderPriority`):
  `"High Priority"` (danger), `"Medium Priority"` (amber), `"Low Priority"`
  (neutral). If that choice list ever changes, update the string match here too.
- The reminder's **title doubles as the H1** — same pattern Google Calendar
  invite emails use (the event title is the headline, not a generic "You have
  a new reminder").
