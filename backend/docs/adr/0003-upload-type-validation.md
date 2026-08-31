# ADR-0003 — Validate upload type by content signature, not the declared `Content-Type`

**Status:** Accepted, implemented
**Date:** 2026-08-31
**Related:** `apps/core/uploads.py`, `apps/meetings/services.py`, `apps/document_hub/`, `apps/contacts/`, `apps/events/`

---

## Context

`apps/core/uploads.py` is the single type-and-size gate for all eleven writable file fields
in the project (ADR-0001's sibling consolidation — one check instead of two partial ones).
Its type half reads the `Content-Type` the client attached to the multipart part:

```python
content_type = getattr(value, "content_type", None)
if content_type and content_type not in allowed_types:
    readable = ", ".join(t.split("/")[-1].upper() for t in allowed_types)
    raise ValidationError(f"{label} must be one of: {readable}.")
```

`UploadedFile.content_type` is not something Django derives. It is copied verbatim out of the
`Content-Type` header of the multipart part, which means the value being validated is supplied
by the caller. That makes the check wrong in both directions.

**False rejections — the one we hit.** On 2026-08-31, `POST
/meetings/<id>/prep/<item>/fields/<field>/respond/` refused a genuine PDF with
`'8650630.pdf' must be one of: PDF, JPEG, PNG, WEBP.` The file was a valid PDF; the client
had simply labelled the part `application/octet-stream`. That is not an exotic client bug —
it is the documented fallback for any sender that does not maintain an extension→MIME table,
and it covers a long tail of real callers:

- Postman when its file reference cannot be resolved (how this was found).
- `curl -F` where the type is not given explicitly.
- Several mobile HTTP clients uploading from a document picker rather than a gallery.
- Browsers uploading a file whose extension the OS does not recognise.

The user-visible result is a 400 that names the file and tells them the file is the wrong
type when it is not, with no action they can take. Three of these fields are client-writable
(prep-item uploads, contact photos, event day galleries), so the people most likely to hit it
are the least able to diagnose it.

**False acceptances.** The mirror image, and the reason "just add `application/octet-stream`
to the tuple" is not the fix. The header is attacker-chosen, so any file whatsoever can be
labelled `application/pdf` and stored. The gate currently provides no assurance about content
at all — it only asks the caller to assert something and takes the answer.

**What the size half already gets right.** `value.size` is measured by Django from the bytes
actually received. It cannot be spoofed by a header. The type half is the odd one out.

## Decision

**Validate the file's leading bytes. Treat `content_type` as a hint, never as the authority.**

Keep `validate_upload` as the single entry point, keep its signature, and keep `allowed_types`
expressed as MIME strings so all eleven call sites and their tests are untouched. Replace the
body of the type check with a signature probe.

### Signature table

| MIME | Leading bytes |
|---|---|
| `application/pdf` | `%PDF-` |
| `image/jpeg` | `\xFF\xD8\xFF` |
| `image/png` | `\x89PNG\r\n\x1a\n` |
| `image/webp` | `RIFF` + 4 size bytes + `WEBP` (bytes 0–3 and 8–11) |

Twelve bytes is enough to discriminate all four. Read a small prefix, match, then `seek(0)`
so the storage backend still sees the file from the start — the probe must leave the handle
exactly as it found it, or R2 writes a truncated object.

### Rules

1. **Signature matched and in `allowed_types`** → accept, whatever the header said.
2. **Signature matched but not in `allowed_types`** → reject, naming what it actually is
   ("`x.pdf` is a PDF; this field takes JPEG, PNG, WEBP"). Strictly better than today's
   message, which can only repeat the whitelist.
3. **No signature matched** → reject. This is the case that used to slip through whenever the
   caller declared a permitted type.
4. **`value` has no readable bytes** (a file loaded from storage on a partial update rather
   than posted) → skip, exactly as the current code skips a missing `content_type`. This
   exemption is load-bearing: without it every PATCH that leaves a file field untouched would
   start failing.

The declared `content_type` stops being consulted for the accept/reject decision. Worth
logging when it disagrees with the signature — a sustained disagreement rate is either a
client bug worth fixing or someone probing the gate.

## Consequences

### What we gain

- Honest clients stop being refused for how they labelled a part. This closes a class of
  support ticket that is effectively undiagnosable from the user's side.
- The gate makes a claim it can actually back: a stored `.pdf` really begins with `%PDF-`.
- Error messages can name the real type instead of restating the whitelist.
- Nothing at the call sites changes — one function body, eleven fields inherit it.

### What we lose / accept

| Cost | Note |
|---|---|
| **A signature is not a full parse.** `%PDF-` followed by garbage still passes. | Accepted. This is a storage-cost and obvious-mistake gate, not a malware scanner, and it is strictly stronger than trusting a header. Rendering safety is the frontend's concern. |
| **A PDF with leading junk before `%PDF-`** is rejected, though some readers tolerate it. | Accepted; such files are rare and malformed. If real ones appear, widen the probe to search the first 1KB rather than byte 0. |
| **Four formats hardcoded.** | The whitelist was already four values. A new format needs a signature added alongside its MIME string — a deliberate two-line change, not a silent one. |
| **A few bytes read per upload.** | Negligible against the multipart read gunicorn has already paid for (see ADR-0001's note on `--timeout 120`). |

### Migration note

Existing rows were validated under the old rule, so some stored blobs may not match their
recorded type. This change is not retroactive and no backfill is proposed — the sweep in
`cleanup_orphaned_documents` is about orphans, not content. If an audit is ever wanted, it is
a separate one-off command.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| **Add `application/octet-stream` to the whitelist** | The one-line fix, and the wrong one. It makes the false-rejection stop and the false-acceptance total: every file of any kind sails through under the generic label. |
| **Keep trusting `content_type`, document the limitation** | The status quo. It refuses legitimate client uploads on three client-writable fields, which is a product defect independent of the security argument. |
| **Validate the filename extension instead** | Equally caller-controlled, and worse: `.pdf` is even easier to attach than a header. |
| **`python-magic` / libmagic** | Correct and far more thorough, but it is a C library that must exist in the Render image. A system dependency in the build for four fixed signatures we can match in a dozen bytes. Revisit if the accepted-format list ever grows past a handful. |
| **Pillow `Image.verify()` for the image types** | Only covers three of the four, decodes the whole file, and `ImageField` already reaches for Pillow — `apps/core/uploads.py`'s own docstring records that Pillow is *not* a size or type control. |
| **Move the check to the edge (Cloudflare / a proxy rule)** | Cannot see the multipart part boundaries usefully, and would split a single documented gate across two systems. The body-size limit belongs at the edge (per the module docstring); type does not. |
