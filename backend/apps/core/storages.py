"""
apps/core/storages.py

Media storage policy — two tiers, so display images and sensitive documents
don't share one URL scheme:

The split is by **whether the URL itself should be a credential**, not by file
type. An object key is not a secret: once a bucket is public the URL *is* the
entire access control, and R2 exposes public access per BUCKET — you cannot make
one object public inside a private bucket. Hence two buckets.

  * **Access-controlled** (Service Agreements, quotations, invoices, receipts,
    budget receipts, meeting prep uploads, ``EventContact.photo``) stay on the
    *default* storage, which — when R2 is on — signs every URL with an expiry
    (``AWS_QUERYSTRING_AUTH=True`` / ``AWS_QUERYSTRING_EXPIRE``). These are never
    handed to a client as a raw storage URL; they are minted on demand by
    ``GET /files/<type>/<id>/`` after an ownership check, with a 60-second expiry
    (see apps/core/filelinks.py). Expiry is what makes a forwarded link stop
    working and what makes revocation take effect.

  * **World-readable** (``EventImage.image`` — the event and event-day galleries —
    and ``TeamMember.photo``) are served from a dedicated **public** bucket on a
    custom domain with **unsigned, permanent** URLs via ``PublicMediaStorage``.
    Signing these is actively harmful: the URLs are cached in already-fetched
    JSON, in the CDN, in the browser and in a static site build, so a signature
    turns every one of those copies into a broken image when it expires. There is
    also nothing to protect — the galleries exist to be on a public website.

Why ``EventContact.photo`` moved to the private tier: contacts are the client's
family, bridal party and vendors, so their photographs are not portfolio
subjects, and they only ever render inside an authenticated portal.

Why ``TeamMember.photo`` moved to the public tier: it is the agency's own staff,
the same handful of photos shown to every client. On the signed tier it paid the
full cost of privacy for none of the benefit — the signature changes on every
serialization, so the URL changes, so the browser cache never hits.

Wiring: the world-readable fields use ``storage=select_public_media_storage``;
everything else simply omits ``storage=`` and inherits the default.
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
