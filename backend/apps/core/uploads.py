"""
apps/core/uploads.py

The size and type ceiling for every writable file field in the project.

The hole this closes
--------------------
This used to be a check per serializer, and there were two of them for eleven
fields. ``meetings`` validated type and size; ``budgets`` validated type only.
``Contact.photo``, ``Event.featured_image``, ``EventDay.event_images``,
``TeamMember.photo`` and the five ``document_hub`` FileFields validated neither
— so a 500MB file was a valid upload, and three of those fields are writable by
a **client**, not just staff.

``ImageField`` is not a size control. Pillow rejects a file that is not an
image, which is why nothing worse than a very large JPEG could land — but a very
large JPEG is the whole problem: it is billed as R2 storage forever, and it is
served back to every client that loads the event.

What a size ceiling does and does not buy
-----------------------------------------
It bounds storage cost, and it turns "the upload quietly succeeded and now costs
money every month" into a 400 the frontend can show.

It does **not** protect the web worker. gunicorn reads the entire request body
before the view runs, so by the time ``file.size`` is readable the transfer has
already been paid for in worker-seconds. Capping that needs a body limit at the
edge, which is a platform setting rather than a Django one. This module is the
second line of that defence, not the first — worth having, not sufficient alone.

Where the numbers come from
---------------------------
Not roundness — the Procfile: ``--workers 3 --threads 4 --timeout 120``. A
request is killed at 120s, and what spends that budget is client *upstream*
bandwidth, which is far worse than the downstream number people quote:

    1 Mbps up (congested mobile)  ->  10MB ~80s    fits
                                  ->  25MB ~200s   killed
    2 Mbps up (ordinary mobile)   ->  10MB ~40s
                                  ->  25MB ~100s   fits, barely
    20 Mbps up (office fibre)     ->  25MB ~10s

So ``MAX_IMAGE_SIZE`` is the largest a client-facing field can be and still
complete from a BAD connection rather than merely an average one — these fields
are written from a venue, on a phone, not from a desk. ``MAX_DOCUMENT_SIZE``
only completes on a good link, which is precisely why it is reserved for
staff-only fields.

~40s is also the patience ceiling: past roughly that, someone on a phone decides
the app is broken and retries, which costs a second upload rather than saving
the first.

Raising any of these without also raising ``--timeout`` buys nothing — the
upload would start failing at the worker instead of at the serializer, which is
a worse error for the same refusal.
"""

from __future__ import annotations

from django.core.files.uploadedfile import UploadedFile
from rest_framework.exceptions import ValidationError

MB = 1024 * 1024

# Headshots and contact photos. A phone camera JPEG is 2-4MB, so this is already
# generous for the thing it holds.
MAX_PHOTO_SIZE = 5 * MB

# Event imagery — covers, day galleries, meeting prep boards. A web-exported
# camera JPEG is 3-8MB; 10MB covers that without inviting an unprocessed RAW.
MAX_IMAGE_SIZE = 10 * MB

# Staff-only documents: contracts, invoices, receipts, the welcome booklet.
# Design-heavy PDFs genuinely run 10-20MB, and no client can write these.
MAX_DOCUMENT_SIZE = 25 * MB

IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
DOCUMENT_TYPES = ("application/pdf", *IMAGE_TYPES)


# ── Content signatures ───────────────────────────────────────────────────────
#
# The type check reads the file's own leading bytes, NOT the Content-Type the
# caller attached to the multipart part. See docs/adr/0003-upload-type-validation.md.
#
# `UploadedFile.content_type` is copied verbatim out of the request, so trusting
# it was wrong in both directions. Honest clients that do not maintain an
# extension->MIME table send `application/octet-stream` for a perfectly good PDF
# (curl -F without an explicit type, several mobile pickers, Postman when its
# file reference goes stale) and were refused with a message telling them the
# file was the wrong type when it was not. Meanwhile anyone could label anything
# `application/pdf` and have it stored unread.
#
# Twelve bytes discriminate all four accepted formats. This is a storage-cost
# and obvious-mistake gate, not a malware scanner: `%PDF-` followed by garbage
# still passes, and that is the documented trade.

_SIGNATURE_PROBE_BYTES = 12

_MAGIC_PREFIXES = (
    ("application/pdf", b"%PDF-"),
    ("image/jpeg", b"\xff\xd8\xff"),
    ("image/png", b"\x89PNG\r\n\x1a\n"),
)


def _sniff_upload_type(upload: UploadedFile) -> str | None:
    """The MIME type an upload's own bytes claim, or None if nothing matches.

    Leaves the handle rewound to 0. The storage backend writes from wherever the
    cursor is left, so skipping the rewind would put a truncated object in R2 —
    the read is invisible in tests that never inspect the stored bytes.
    """
    upload.seek(0)
    head = upload.read(_SIGNATURE_PROBE_BYTES)
    upload.seek(0)

    for mime, prefix in _MAGIC_PREFIXES:
        if head.startswith(prefix):
            return mime
    # WEBP is the odd one out — "RIFF", four bytes of length, then "WEBP".
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _human(size: int) -> str:
    """Bytes as the number a person would say. Whole MB, since every ceiling
    here is a whole number of MB and '10.0MB' reads like false precision."""
    mb = size / MB
    return f"{mb:.0f}MB" if mb == int(mb) else f"{mb:.1f}MB"


def validate_upload(
    value,
    *,
    max_size: int,
    allowed_types: tuple[str, ...] = DOCUMENT_TYPES,
    label: str = "File",
):
    """Reject an upload that is too large or of the wrong type. Returns ``value``.

    Written to be dropped straight into a DRF ``validate_<field>`` hook, so it
    raises DRF's ``ValidationError`` — the whole project raises that one, even
    from services.py, because ``apps.core.exceptions`` only maps that class into
    the standard envelope. Django's identically-named exception would come back
    as a 500.

    The type check runs on the file's leading bytes (``_sniff_upload_type``) and
    ignores the declared ``content_type`` entirely, so a correctly-formed file is
    accepted however the caller labelled it and a mislabelled one is refused on
    what it actually contains.

    Only a freshly POSTed ``UploadedFile`` is sniffed. A ``FieldFile`` handed
    back on a partial update is already in storage, and reading it would mean a
    round trip to R2 on every PATCH that leaves a file field untouched — to
    re-validate bytes that were checked on the way in. Both checks are likewise
    skipped for an empty value, since clearing a field is not an upload.
    """
    if not value:
        return value

    if isinstance(value, UploadedFile):
        permitted = ", ".join(t.split("/")[-1].upper() for t in allowed_types)
        actual = _sniff_upload_type(value)
        if actual is None:
            raise ValidationError(f"{label} must be one of: {permitted}.")
        if actual not in allowed_types:
            # Name what it really is — the old message could only repeat the
            # whitelist back, which is useless when the caller believes they
            # already sent one of those.
            raise ValidationError(
                f"{label} is a {actual.split('/')[-1].upper()}; "
                f"must be one of: {permitted}."
            )

    size = getattr(value, "size", None)
    if size is not None and size > max_size:
        raise ValidationError(
            f"{label} is {_human(size)}, which is over the {_human(max_size)} limit."
        )

    return value


def validate_photo(value):
    """A person's photo — contact, team member."""
    return validate_upload(
        value, max_size=MAX_PHOTO_SIZE, allowed_types=IMAGE_TYPES, label="Photo",
    )


def validate_image(value):
    """Event imagery: covers and day galleries."""
    return validate_upload(
        value, max_size=MAX_IMAGE_SIZE, allowed_types=IMAGE_TYPES, label="Image",
    )


def validate_document(value):
    """A staff-uploaded PDF or scan: contracts, invoices, receipts, booklets."""
    return validate_upload(
        value, max_size=MAX_DOCUMENT_SIZE, allowed_types=DOCUMENT_TYPES, label="File",
    )
