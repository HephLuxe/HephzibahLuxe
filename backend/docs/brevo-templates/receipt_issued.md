# Template: `receipt_issued`

Sent when staff issue a receipt for a completed payment
(`document_hub.views.create_receipt`).

| | |
|---|---|
| `template_name` | `receipt_issued` |
| Env var | `BREVO_TEMPLATE_RECEIPT_ISSUED` |
| Fired by | `document_hub.views.create_receipt` (immediate) |
| Merge fields | `receipt_number`, `amount`, `payment_for`, `event_title` |

**Suggested subject:** `Receipt {{ params.receipt_number }} — payment received`
**Preheader:** `We've received your payment — your receipt is ready.`

Reuses the shared base layout — see [`README.md`](./README.md).

---

## Test parameters

```json
{
  "receipt_number": "HL-PSW001-R003",
  "amount": "3000000.00",
  "payment_for": "Non-refundable Retainer",
  "event_title": "Adaeze & Michael's Wedding"
}
```

## Before you send

1. Replace `LOGO_URL_HERE` with the hosted logo URL.
2. Replace `PORTAL_BASE_URL_HERE` with your deployed frontend root — the CTA
   points at `/portal/financials/client`, the real route for `receipt` targets
   (`apps/core/deeplinks.py`).
3. Paste the subject, save, set `BREVO_TEMPLATE_RECEIPT_ISSUED=<id>`.

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
  <title>Receipt issued</title>
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
    We've received your payment — your receipt is ready.
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
              <div style="font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#B79A66;">Payment received</div>
              <h1 class="h1" style="margin:12px 0 0;font-family:'Cormorant Garamond',Georgia,serif;font-size:36px;line-height:1.15;font-weight:600;color:#062025;">Thank you for your payment</h1>
            </td>
          </tr>

          <!-- copy -->
          <tr>
            <td class="px" style="padding:22px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              We've received your payment toward <strong>{{ params.event_title }}</strong>. Your receipt is below.
            </td>
          </tr>

          <!-- detail card: receipt # | for | amount -->
          <tr>
            <td class="px" style="padding:28px 48px 0;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F8F7F4;border:1px solid #ECEAE4;border-radius:12px;">
                <tr>
                  <td style="padding:22px 28px 14px;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td class="col2" width="50%" valign="top" style="font-family:'Inter',Arial,sans-serif;border-right:1px solid #ECEAE4;">
                          <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Receipt number</div>
                          <div style="margin-top:6px;font-size:16px;color:#062025;font-weight:500;">{{ params.receipt_number }}</div>
                        </td>
                        <td class="col2" width="50%" valign="top" style="font-family:'Inter',Arial,sans-serif;padding-left:28px;">
                          <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Payment for</div>
                          <div style="margin-top:6px;font-size:16px;color:#062025;font-weight:500;">{{ params.payment_for }}</div>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr><td style="padding:0 28px;"><div style="height:1px;background-color:#ECEAE4;line-height:1px;font-size:0;">&nbsp;</div></td></tr>
                <tr>
                  <td style="padding:16px 28px 22px;font-family:'Inter',Arial,sans-serif;">
                    <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Amount paid</div>
                    <div style="margin-top:6px;font-family:'Cormorant Garamond',Georgia,serif;font-size:26px;color:#062025;font-weight:600;">&#8358;{{ params.amount }}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- CTA -->
          <tr>
            <td class="px" align="center" style="padding:32px 48px 4px;">
              <!--[if mso]>
              <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="PORTAL_BASE_URL_HERE/portal/financials/client" style="height:54px;v-text-anchor:middle;width:280px;" arcsize="50%" strokecolor="#062025" fillcolor="#062025">
                <w:anchorlock/>
                <center style="color:#FBFAF7;font-family:Arial,sans-serif;font-size:13px;letter-spacing:2px;font-weight:bold;">VIEW RECEIPT</center>
              </v:roundrect>
              <![endif]-->
              <!--[if !mso]><!-- -->
              <a href="PORTAL_BASE_URL_HERE/portal/financials/client" style="display:inline-block;background-color:#062025;color:#FBFAF7;font-family:'Inter',Arial,sans-serif;font-size:13px;font-weight:600;letter-spacing:2px;text-transform:uppercase;text-decoration:none;padding:17px 40px;border-radius:100px;">View receipt</a>
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

- This is the one financial template that reads as a **thank-you**, not a
  reminder — headline and opening line are warmer/gratitude-toned rather than
  action-prompting, which is why "Amount paid" gets the largest, most
  prominent number treatment in the card (the emotional payoff of the email).
- `receipt_number` format matches the client portal (`HL-<segment>-R<seq>`, e.g.
  `HL-PSW001-R003`, where the `PSW001` segment is the couple/honoree initials +
  event-type letter + global per-event-type count).
