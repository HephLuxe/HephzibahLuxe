# Template: `password_reset`

The password-reset code email. Sent when a user requests a reset
(`accounts.utils.send_password_reset_email`) — a 6-digit code they type into
the app, not a clickable reset link.

| | |
|---|---|
| `template_name` | `password_reset` |
| Env var | `BREVO_TEMPLATE_PASSWORD_RESET` |
| Fired by | `accounts.utils.send_password_reset_email` (immediate) |
| Merge fields | `user_first_name`, `code`, `expires_in_minutes` |

**Suggested subject:** `Your Hephzibah Luxe password reset code`
**Preheader:** `Use the code below to reset your password.`

Reuses the shared base layout — see [`README.md`](./README.md) for tokens and
[`user_credentials.md`](./user_credentials.md) for the annotated skeleton.

---

## Test parameters

```json
{
  "user_first_name": "Amara",
  "code": "482913",
  "expires_in_minutes": 15
}
```

## Before you send

1. Replace `LOGO_URL_HERE` with the hosted logo URL (Brevo image picker).
2. Paste the subject above into the template's subject field.
3. Save, set `BREVO_TEMPLATE_PASSWORD_RESET=<id>`.

No portal link is needed here — the user is already in the app entering the
code, so there's deliberately no CTA button (matches how Google/Stripe send
OTP codes: code, expiry, done).

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
  <title>Your password reset code</title>
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
      .code{font-size:34px!important;letter-spacing:8px!important;}
    }
  </style>
</head>
<body style="margin:0;padding:0;background-color:#F3F1EC;">

  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#F3F1EC;opacity:0;">
    Use the code below to reset your password.
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
              <div style="font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#B79A66;">Security</div>
              <h1 class="h1" style="margin:12px 0 0;font-family:'Cormorant Garamond',Georgia,serif;font-size:36px;line-height:1.15;font-weight:600;color:#062025;">Reset your password</h1>
            </td>
          </tr>

          <!-- copy -->
          <tr>
            <td class="px" style="padding:22px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              Hello {{ params.user_first_name }},
            </td>
          </tr>
          <tr>
            <td class="px" style="padding:16px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              We received a request to reset the password on your Hephzibah Luxe account. Enter the code below to continue.
            </td>
          </tr>

          <!-- code panel -->
          <tr>
            <td class="px" style="padding:28px 48px 0;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F8F7F4;border:1px solid #ECEAE4;border-radius:12px;">
                <tr>
                  <td align="center" style="padding:32px 28px;font-family:'Inter',Arial,sans-serif;">
                    <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Your reset code</div>
                    <div class="code" style="margin-top:12px;font-family:'Courier New',Consolas,monospace;font-size:40px;letter-spacing:12px;color:#062025;font-weight:700;">{{ params.code }}</div>
                    <div style="margin-top:14px;font-size:13px;color:#7A8385;">Expires in {{ params.expires_in_minutes }} minutes</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- note -->
          <tr>
            <td class="px" style="padding:26px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:14px;line-height:1.7;color:#7A8385;">
              If you didn't request this, you can safely ignore this email — your password will remain unchanged.
            </td>
          </tr>

          <!-- signature -->
          <tr>
            <td class="px" style="padding:26px 48px 40px;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
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

- `code` is a 6-digit numeric string (`accounts.utils.generate_reset_code`) —
  displayed large, letter-spaced, monospace so it's easy to read and re-type.
- No CTA button is intentional — this code is entered back in the app the
  user already has open, not clicked from the email.
- `PasswordResetToken` is single-use and invalidates prior unused tokens for
  the same user (see `create_password_reset_token`), so the "ignore if you
  didn't request this" line is safe/accurate — an unused old code simply stops
  working once a new one is issued.
