import os
from decimal import Decimal, InvalidOperation


# ── Attribution ──────────────────────────────────────────────────

def user_display_name(user) -> str:
    """
    Human-readable name for a User FK (e.g. a `last_updated_by` / `phase_updated_by`
    field), computed at read time so it always reflects the account's *current*
    name. Falls back to the email, then "". Shared by the "Last Updated by …"
    attribution across contacts, events, and the planning phase.
    """
    if not user:
        return ""
    return f"{user.first_name} {user.last_name}".strip() or user.email


def _real_actor(user):
    """True for an authenticated User we can attribute a write to."""
    return bool(user) and not getattr(user, "is_anonymous", False)


def save_with_attribution(serializer, user, **extra):
    """
    serializer.save(), stamping the acting user: last_updated_by always, plus
    created_by when this is a create (serializer.instance is None).

    The attribution FKs are editable=False and never appear in serializer
    `fields`, so passing them as save() kwargs sets them straight on the model —
    a client can't spoof them by putting them in the request body.

        save_with_attribution(serializer, request.user, event=event)
    """
    if _real_actor(user):
        if serializer.instance is None:
            extra.setdefault("created_by", user)
        extra.setdefault("last_updated_by", user)
    return serializer.save(**extra)


def stamp_attribution(instance, user, *, creating=None):
    """
    Same stamping for service/ORM paths that build an instance directly and then
    call .save(). `creating` defaults to whether the row is still unsaved.
    Silently no-ops for models that don't carry the fields, so it's safe to call
    from shared helpers.
    """
    if not _real_actor(user):
        return instance
    if creating is None:
        creating = instance._state.adding
    if creating and hasattr(instance, "created_by"):
        instance.created_by = user
    if hasattr(instance, "last_updated_by"):
        instance.last_updated_by = user
    return instance


# ── Money ────────────────────────────────────────────────────────

def parse_decimal(value) -> Decimal | None:
    """
    Coerce a raw request-body value (str, int, float, or already-Decimal) into
    a Decimal, or return None if it isn't a valid number.

    Money fields assigned directly from `request.data.get(...)` without going
    through a serializer keep whatever Python type the client sent (a bare
    JSON string, or a float) — arithmetic against a Decimal model property
    (e.g. `total_budget - spent`) then raises TypeError, not a clean 400. Every
    view that reads a money field straight off request.data should route it
    through this first.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


# ── internal helper ──────────────────────────────────────────────

def _safe_portal_id(instance):
    """Walk the chain to portal.id. Returns 'unknown' gracefully if any link is null."""
    try:
        return str(instance.get_portal_id())
    except (AttributeError, TypeError, ValueError):
        return "unknown"


# ── Event ────────────────────────────────────────────────────────
# Keyed by the immutable event.pk, not the slug — the slug is kept only as a
# cosmetic suffix for anyone browsing storage directly. Uniqueness/collision
# safety comes from the pk, so the slug can change later (a rename, a future
# feature) without ever orphaning an already-stored file path — the old
# slug-only scheme couldn't make that guarantee (see
# docs/FAILURE_POINTS_AUDIT.md F9 and HEPHZIBAH_LUXE_AUDIT_AND_PLAN.md §4.3).
# No slug regeneration/reclaim logic is introduced by this change — slugs
# still behave exactly as before; only the storage folder naming changes.

def event_cover_upload_path(instance, filename):
    """
    instance = Event
    portals/{portal_id}/events/{event_id}-{event_slug}/covers/cover.{ext}
    """
    portal_id = _safe_portal_id(instance)
    event_id = instance.pk or "new"
    slug = getattr(instance, "slug", None) or "no-slug"
    ext = os.path.splitext(filename)[1]
    return f"portals/{portal_id}/events/{event_id}-{slug}/covers/cover{ext}"


def event_image_upload_path(instance, filename):
    """
    instance = EventDay
    portals/{portal_id}/events/{event_id}-{event_slug}/days/{day_id}/images/image.{ext}
    """
    portal_id = _safe_portal_id(instance)
    owner = getattr(instance, "owner", None)
    event_id = getattr(owner, "pk", None) or "new"
    slug = getattr(owner, "slug", None) or "no-slug"
    day_id = instance.pk or "new"
    ext = os.path.splitext(filename)[1]
    return f"portals/{portal_id}/events/{event_id}-{slug}/days/{day_id}/images/image{ext}"


# ── Contacts ─────────────────────────────────────────────────────

def contact_photo_upload_path(instance, filename):
    """
    instance = EventContact
    portals/{portal_id}/events/{event_id}-{event_slug}/contacts/{contact_id}/photo.{ext}
    """
    portal_id = _safe_portal_id(instance)
    event = getattr(instance, "event", None)
    event_id = getattr(event, "pk", None) or "new"
    slug = getattr(event, "slug", None) or "no-slug"
    contact_id = instance.pk or "new"
    ext = os.path.splitext(filename)[1]
    return f"portals/{portal_id}/events/{event_id}-{slug}/contacts/{contact_id}/photo{ext}"


# ── Budget ───────────────────────────────────────────────────────

def budget_receipt_upload_path(instance, filename):
    """
    instance = BudgetPayment
    portals/{portal_id}/events/{event_id}-{event_slug}/budget/receipts/{filename}
    """
    try:
        event = instance.budget.event
        portal_id = str(event.celebrant.portal.id)
        event_id = event.pk or "new"
        slug = event.slug or "no-slug"
    except AttributeError:
        portal_id = "unknown"
        event_id = "unknown"
        slug = "unknown"
    name, ext = os.path.splitext(filename)
    return f"portals/{portal_id}/events/{event_id}-{slug}/budget/receipts/{name}{ext}"


# ── Meeting prep uploads ─────────────────────────────────────────

def prep_upload_path(instance, filename):
    """
    instance = PrepItemFileUpload
    portals/{portal_id}/meetings/{meeting_id}/prep/{field_id}/{filename}
    """
    try:
        portal_id = str(instance.get_portal_id())
        meeting_id = instance.field.prep_item.meeting.pk
        field_id = instance.field.pk
    except AttributeError:
        portal_id = "unknown"
        meeting_id = "unknown"
        field_id = "unknown"
    return f"portals/{portal_id}/meetings/{meeting_id}/prep/{field_id}/{filename}"


# ── Document Hub ─────────────────────────────────────────────────
# Scoped by event, like the Event section above — keyed by the engagement's
# event.pk, not its slug, so a portal with multiple engagements over time
# (e.g. a wedding this year, an anniversary next year) keeps each event's
# documents/invoices/receipts separated instead of all piling into one flat
# per-portal folder.

def _event_folder(engagement) -> str:
    """Build the '{event_id}-{event_slug}' folder name from an EventEngagement."""
    event = getattr(engagement, "event", None)
    event_id = getattr(event, "pk", None) or "unknown"
    slug = getattr(event, "slug", None) or "no-slug"
    return f"{event_id}-{slug}"


def client_document_upload_path(instance, filename):
    """
    instance = ClientDocument
    portals/{portal_id}/events/{event_id}-{event_slug}/documents/{category}/{filename}
    """
    portal_id = _safe_portal_id(instance)
    event_folder = _event_folder(getattr(instance, "engagement", None))
    return f"portals/{portal_id}/events/{event_folder}/documents/{instance.category}/{filename}"


def invoice_upload_path(instance, filename):
    """
    instance = Invoice
    portals/{portal_id}/events/{event_id}-{event_slug}/invoices/{filename}
    """
    portal_id = _safe_portal_id(instance)
    event_folder = _event_folder(getattr(instance, "engagement", None))
    return f"portals/{portal_id}/events/{event_folder}/invoices/{filename}"


def receipt_upload_path(instance, filename):
    """
    instance = Receipt
    portals/{portal_id}/events/{event_id}-{event_slug}/receipts/{filename}
    """
    portal_id = _safe_portal_id(instance)
    event_folder = _event_folder(getattr(instance, "engagement", None))
    return f"portals/{portal_id}/events/{event_folder}/receipts/{filename}"