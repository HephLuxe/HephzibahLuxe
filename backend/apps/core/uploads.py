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

    Both checks are skipped when the attribute is missing rather than assumed
    hostile. ``content_type`` is absent on a file that came from storage instead
    of a multipart POST, and refusing those would break every partial update that
    happens to include an untouched file field.
    """
    if not value:
        return value

    content_type = getattr(value, "content_type", None)
    if content_type and content_type not in allowed_types:
        readable = ", ".join(t.split("/")[-1].upper() for t in allowed_types)
        raise ValidationError(f"{label} must be one of: {readable}.")

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
