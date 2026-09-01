"""
apps/core/storages.py

Media storage policy — two tiers, so display images and sensitive documents
don't share one URL scheme:

  * **Documents** (Service Agreements, quotations, invoices, receipts, budget
    receipts, meeting prep uploads) stay on the *default* storage, which — when
    R2 is on — signs every URL with a short expiry (``AWS_QUERYSTRING_AUTH=True``
    / ``AWS_QUERYSTRING_EXPIRE``, see config/settings.py). A leaked or shared
    link stops working once it expires. Nothing here changes that.

  * **Display images** (``EventImage.image`` — the event and event-day galleries —
    and ``EventContact.photo``) are shown inline by the frontend and are routinely
    cached in an already-fetched event JSON payload. A 1-hour signed URL would
    turn into a broken image an hour after the page was loaded. So these are
    served from a dedicated **public** bucket / custom domain with **unsigned,
    long-lived** URLs via ``PublicMediaStorage`` below.

Wiring: those image fields use ``storage=select_public_media_storage``.
Because that's a *callable*, Django records only the reference in migrations and
resolves the actual backend at boot from the current settings — so flipping
``USE_R2_STORAGE`` (or configuring a public bucket later) needs no new migration.

Fail-safe by design: if R2 is enabled but no public bucket/domain is configured
yet, images fall back to the default *signed* storage rather than emitting
unsigned URLs against a private bucket (which would 403). The worst case is
therefore "images work, but with a 1h signature" — never "images break".
"""

from django.conf import settings
from django.core.files.storage import InMemoryStorage, default_storage


def _public_media_configured() -> bool:
    """True once a dedicated public bucket or custom domain has been set."""
    return bool(
        getattr(settings, "R2_PUBLIC_URL", "")
        or getattr(settings, "R2_PUBLIC_BUCKET_NAME", "")
    )


def select_public_media_storage():
    """
    Storage callable for the display-image fields. Resolved at boot:

      * ``USE_R2_STORAGE`` off (tests/CI only)         -> in-memory storage (RAM)
      * R2 on, no public bucket/domain configured yet  -> default *signed* storage
      * R2 on, public bucket/domain configured         -> PublicMediaStorage (unsigned)

    There is no local-disk (FileSystemStorage) path — media lives on R2 in every
    real environment; the in-memory fallback exists only so the suite can run
    without R2 credentials.
    """
    if not getattr(settings, "USE_R2_STORAGE", False):
        return InMemoryStorage()
    if _public_media_configured():
        return PublicMediaStorage()
    return default_storage


def PublicMediaStorage(**kwargs):  # noqa: N802 (factory named like a class on purpose)
    """
    Build an S3 storage bound to the public bucket/domain with signing off.

    Defined as a factory (not a module-level ``class``) so importing this module
    never requires ``storages``/``boto3`` — it's only pulled in when R2 is on.
    """
    from storages.backends.s3 import S3Storage

    class _PublicMediaStorage(S3Storage):
        def __init__(self, **opts):
            opts.setdefault("querystring_auth", False)  # unsigned, public URLs
            opts.setdefault("file_overwrite", False)     # never clobber same-named uploads
            opts.setdefault("default_acl", None)         # R2 has no per-object ACLs
            public_bucket = getattr(settings, "R2_PUBLIC_BUCKET_NAME", "")
            if public_bucket:
                opts.setdefault("bucket_name", public_bucket)
            public_domain = getattr(settings, "R2_PUBLIC_URL", "")
            if public_domain:
                # Strip scheme — django-storages wants a bare host for custom_domain.
                opts.setdefault(
                    "custom_domain",
                    public_domain.replace("https://", "").replace("http://", "").rstrip("/"),
                )
            super().__init__(**opts)

    return _PublicMediaStorage(**kwargs)
