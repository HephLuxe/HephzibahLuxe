# Template: `user_credentials`

**The account-onboarding email.** Sent immediately when a new celebrant account
is created (`accounts.utils.send_user_credentials_email`) with their first-time
sign-in details.

| | |
|---|---|
| `template_name` | `user_credentials` |
| Env var | `BREVO_TEMPLATE_USER_CREDENTIALS` |
| Fired by | `accounts.utils.send_user_credentials_email` (immediate) |
| Merge fields | `display_name`, `user_email`, `temporary_password`, `login_url` |

**Suggested subject (set inside Brevo):** `Your Hephzibah Luxe account is ready`
**Preheader:** `Your private client portal is ready — here are your secure sign-in details.`

This is also the canonical **base layout**. Every other template reuses this
exact skeleton (head, header band, footer band, button) and only swaps the body
rows in the middle. See [`README.md`](./README.md) for the shared tokens.

---

## Test parameters (paste into Brevo's "Test parameters" panel)

```json
{
  "display_name": "Amara Okafor",
  "user_email": "amara.okafor@example.com",
  "temporary_password": "Xk7mQ2pL9vTn",
  "login_url": "https://portal.hephzibahluxe.com/sign-in"
}
```

## Before you send — one-time setup

1. Upload **`HEPHZIBAH LUXE LOGO.png`** in Brevo's image picker and replace
   `LOGO_URL_HERE` (2 places would be unusual — it appears once here) with the
   hosted URL. It's a **white** logo, so it must stay on the dark header band.
2. Paste the subject above into the template's subject field.
3. Save, copy the numeric template ID, set `BREVO_TEMPLATE_USER_CREDENTIALS=<id>`.

---

## Full HTML — copy/paste into Brevo (Code your own)

```html
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
  <title>Your Hephzibah Luxe account</title>
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

  <!-- preheader (hidden preview text) -->
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#F3F1EC;opacity:0;">
    Your private client portal is ready — here are your secure sign-in details.
    &#8199;&#65279;&#8199;&#65279;&#8199;&#65279;&#8199;&#65279;&#8199;&#65279;&#8199;&#65279;&#8199;&#65279;
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F3F1EC;">
    <tr>
      <td align="center" style="padding:32px 16px;">

        <!-- ══ CARD ══ -->
        <table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;background-color:#FFFFFF;border-radius:16px;overflow:hidden;border:1px solid #ECEAE4;">

          <!-- ── header band ── -->
          <tr>
            <td align="center" style="background-color:#062025;padding:38px 40px 30px;">
              <img src="LOGO_URL_HERE" width="150" alt="Hephzibah Luxe" style="display:block;width:150px;max-width:150px;height:auto;margin:0 auto 12px;">
              <div style="font-family:'Cormorant Garamond',Georgia,serif;font-size:13px;letter-spacing:3px;text-transform:uppercase;color:#C9B48A;">Event Planning &amp; Design Studio</div>
            </td>
          </tr>

          <!-- ── eyebrow + heading ── -->
          <tr>
            <td class="px" style="padding:46px 48px 0;">
              <div style="font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#B79A66;">Welcome</div>
              <h1 class="h1" style="margin:12px 0 0;font-family:'Cormorant Garamond',Georgia,serif;font-size:36px;line-height:1.15;font-weight:600;color:#062025;">Your journey begins here</h1>
            </td>
          </tr>

          <!-- ── copy ── -->
          <tr>
            <td class="px" style="padding:22px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              Dear {{ params.display_name }},
            </td>
          </tr>
          <tr>
            <td class="px" style="padding:16px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              Welcome to Hephzibah Luxe. Your private client portal has been created — a dedicated space where every detail of your celebration is thoughtfully organised in one place. Use the details below to sign in for the first time.
            </td>
          </tr>

          <!-- ── credential card ── -->
          <tr>
            <td class="px" style="padding:28px 48px 0;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F8F7F4;border:1px solid #ECEAE4;border-radius:12px;">
                <tr>
                  <td style="padding:24px 28px 14px;font-family:'Inter',Arial,sans-serif;">
                    <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Email</div>
                    <div style="margin-top:6px;font-size:16px;color:#062025;font-weight:500;word-break:break-all;">{{ params.user_email }}</div>
                  </td>
                </tr>
                <tr><td style="padding:0 28px;"><div style="height:1px;background-color:#ECEAE4;line-height:1px;font-size:0;">&nbsp;</div></td></tr>
                <tr>
                  <td style="padding:16px 28px 24px;font-family:'Inter',Arial,sans-serif;">
                    <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#8A9092;">Temporary password</div>
                    <div style="margin-top:8px;font-family:'Courier New',Consolas,monospace;font-size:22px;letter-spacing:2px;color:#062025;font-weight:700;">{{ params.temporary_password }}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- ── CTA (bulletproof pill button) ── -->
          <tr>
            <td class="px" align="center" style="padding:32px 48px 4px;">
              <!--[if mso]>
              <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="{{ params.login_url }}" style="height:54px;v-text-anchor:middle;width:280px;" arcsize="50%" strokecolor="#062025" fillcolor="#062025">
                <w:anchorlock/>
                <center style="color:#FBFAF7;font-family:Arial,sans-serif;font-size:13px;letter-spacing:2px;font-weight:bold;">SIGN IN TO YOUR PORTAL</center>
              </v:roundrect>
              <![endif]-->
              <!--[if !mso]><!-- -->
              <a href="{{ params.login_url }}" style="display:inline-block;background-color:#062025;color:#FBFAF7;font-family:'Inter',Arial,sans-serif;font-size:13px;font-weight:600;letter-spacing:2px;text-transform:uppercase;text-decoration:none;padding:17px 40px;border-radius:100px;">Sign in to your portal</a>
              <!--<![endif]-->
            </td>
          </tr>

          <!-- ── security note ── -->
          <tr>
            <td class="px" style="padding:26px 48px 0;font-family:'Inter',Arial,sans-serif;font-size:14px;line-height:1.7;color:#7A8385;">
              For your security, please keep this password private and change it after your first sign-in. If the button doesn’t work, copy and paste this link into your browser:
              <a href="{{ params.login_url }}" style="color:#B79A66;text-decoration:underline;word-break:break-all;">{{ params.login_url }}</a>
            </td>
          </tr>

          <!-- ── signature ── -->
          <tr>
            <td class="px" style="padding:26px 48px 40px;font-family:'Inter',Arial,sans-serif;font-size:16px;line-height:1.7;color:#4A5456;">
              With warm regards,<br>
              <span style="font-family:'Cormorant Garamond',Georgia,serif;font-size:20px;color:#062025;">The Hephzibah Luxe Team</span>
            </td>
          </tr>

          <!-- ── footer band ── -->
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
        <!-- ══ /CARD ══ -->

      </td>
    </tr>
  </table>
</body>
</html>
```

---

## Notes specific to this email

- **`login_url`** comes from `apps.core.deeplinks.login_url()` — it's already a
  full absolute URL, safe to drop straight into `href`.
- The temporary password is shown in plaintext by design (it's how the user
  first gets in). The security line nudges them to change it after sign-in — keep
  that line; don't remove it for "cleaner" copy.
- No expiry field is passed for credentials (unlike `password_reset`, which has
  `expires_in_minutes`), so the copy makes no expiry claim. If you later want the
  temp password to expire, add the field at the call site first, then surface it
  here.
- Long emails/passwords won't break the card — `word-break:break-all` is set on
  both value rows.
