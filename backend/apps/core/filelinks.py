"""
apps/core/filelinks.py

The registry that turns a *private file field* into a short-lived, authorised
URL — the read path for everything on the signed storage tier.

Sibling of ``deeplinks.py``, deliberately the same shape: a slug keys a spec,
the spec names the model lazily by label, and each spec carries its own
ownership resolver so the permission check lives next to the route definition
rather than in the view. Adding a seventh private file field is one entry here.

The hole this closes
--------------------
Serializers used to emit the raw storage URL for these fields, which meant the
API handed the client a pre-signed R2 link valid for a full hour
(``AWS_QUERYSTRING_EXPIRE``). Three problems followed from that, and they pull
in opposite directions, which is why no single value of the expiry fixed them:

* **The link went stale.** The signature is minted when the response is
  serialized, so a client who opened the portal, went into a meeting, and came
  back two hours later clicked "Service Agreement" and got a 403. The page
  looked healthy; only the click failed.
* **The link outlived the permission.** Deleting a document, deactivating an
  account or ending an engagement did nothing to a signature already handed out.
  Access really stopped up to an hour after the platform said it had.
* **A forwarded link kept working.** A URL pasted into a family WhatsApp group
  was an hour of unauthenticated access to an invoice, for everybody in it.

Lengthening the expiry fixes the first and worsens the other two; shortening it
does the reverse. Minting on demand escapes the trade entirely: the client holds
*this* endpoint's URL, which never expires and is safe to cache, while the
storage signature it produces lives 60 seconds. The stale-tab case disappears
(the endpoint re-signs on request), revocation is immediate (ownership is
re-checked at click time, not at page-render time), and the leak window shrinks
by a factor of sixty.

Why JSON and not a 302
----------------------
Authentication here is a bearer token
(``DEFAULT_AUTHENTICATION_CLASSES = JWTAuthentication``), and browsers do not
attach custom headers to browser-initiated requests — neither
``<img src="...">`` nor a clicked ``<a href="...">`` carries an
``Authorization`` header, so both would arrive anonymous and get a 401. A
redirect endpoint is therefore only usable from ``fetch()``, which for an inline
image means routing the bytes through a blob URL and losing browser caching.
Returning ``{"url": ..., "expires_in": ...}`` lets the frontend make one
authenticated JSON call and then use the result directly in ``src``/``href``,
with the same security properties and less client code.

Public files are NOT here
-------------------------
``EventImage.image`` and ``TeamMember.photo`` live on the public bucket behind a
custom domain and are emitted as plain, permanent URLs. Routing them through
this endpoint would add a Django request per photo — a portfolio page of twenty
images becomes twenty worker-thread occupancies — defeat CDN caching, and hide
nothing: the browser follows any redirect and the final URL is visible in the
network tab regardless. See apps/core/storages.py for the tier split.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from django.apps import apps
from django.db.models import Model

# How long a minted signature lives. Seconds, not the hour that
# AWS_QUERYSTRING_EXPIRE gives a directly-serialized URL: the client is expected
# to use it immediately (the frontend has just asked for it), so the only thing a
# longer window buys is a wider leak if the URL is shared. Long enough to absorb
# a slow connection and a clock skew, short enough that a pasted link is dead
# before anyone can click it.
MINTED_URL_EXPIRY_SECONDS = 60


@dataclass(frozen=True)
class FileSpec:
    """How to find — and authorise — one kind of private file."""

    model_label: str                          # "app_label.ModelName" (lazy: avoids import cycles)
    field: str                                # the FileField/ImageField attribute name
    engagement: Callable[[Model], object]     # obj -> EventEngagement | None (ownership check)
    label: str                                # human description, used in error messages

    def get_model(self) -> type[Model]:
        return apps.get_model(self.model_label)


# ── Engagement resolvers ─────────────────────────────────────────
# Every private file must be able to name the engagement it belongs to, or the
# ownership check cannot be made. Written defensively, exactly as deeplinks.py
# is: an event with no engagement wired up yet (see FAILURE_POINTS_AUDIT F3/F7)
# resolves to None, which the view treats as "not authorised" — never as
# "allowed". Fail-closed is the only safe default for a file-serving endpoint.

def _engagement_of_event(event):
    return getattr(event, "engagement", None)


FILE_TYPES: dict[str, FileSpec] = {
    # ── Document hub ──
    "client-document": FileSpec(
        model_label="document_hub.ClientDocument",
        field="file",
        engagement=lambda o: o.engagement,
        label="document",
    ),
    "invoice": FileSpec(
        model_label="document_hub.Invoice",
        field="file",
        engagement=lambda o: o.engagement,
        label="invoice",
    ),
    "receipt": FileSpec(
        model_label="document_hub.Receipt",
        field="file",
        engagement=lambda o: o.engagement,
        label="receipt",
    ),

    # ── Event budget ──
    "budget-receipt": FileSpec(
        model_label="budgets.BudgetPayment",
        field="receipt",
        engagement=lambda o: _engagement_of_event(o.budget.event),
        label="payment receipt",
    ),

    # ── Meetings ──
    "prep-upload": FileSpec(
        model_label="meetings.PrepItemFileUpload",
        field="file",
        engagement=lambda o: o.field.prep_item.meeting.engagement,
        label="preparation upload",
    ),

    # ── Contacts ──
    "contact-photo": FileSpec(
        model_label="contacts.EventContact",
        field="photo",
        engagement=lambda o: _engagement_of_event(o.event),
        label="contact photo",
    ),
}

# NOTE: PortalDefaults' three template files (service agreement, welcome
# booklet, FAQ) are deliberately absent. They are portal-wide staff templates
# with no engagement to key an ownership check on, so they cannot be authorised
# by the resolver above. They are served through the document hub's own views,
# which scope by portal directly.


def spec_for_type(file_type: str) -> FileSpec | None:
    """Look up a spec by its public ``file_type`` slug. None if unregistered."""
    return FILE_TYPES.get(file_type)


def file_url_path(file_type: str, obj_id) -> str:
    """
    The API path that mints a URL for this object's file.

    Built here rather than with ``reverse()`` so serializers can produce it
    without a request in context — the same reason deeplinks.py builds portal
    routes as plain strings.
    """
    return f"/api/v1/files/{file_type}/{obj_id}/"


def mint_url(instance: Model, spec: FileSpec) -> str | None:
    """
    A freshly signed, short-lived URL for ``instance``'s file, or None if the
    field is empty.

    ``expire`` is passed per call rather than set globally: the same storage
    backend still serves anything serialized directly, and that path wants the
    ordinary ``AWS_QUERYSTRING_EXPIRE``. django-storages' S3Storage.url()
    accepts ``expire``; a backend that does not (the in-memory storage used in
    tests) is called without it.
    """
    file = getattr(instance, spec.field, None)
    if not file:
        return None
    try:
        return file.storage.url(file.name, expire=MINTED_URL_EXPIRY_SECONDS)
    except TypeError:
        # InMemoryStorage/FileSystemStorage take no `expire`. Nothing is signed
        # there either, so there is no expiry to shorten.
        return file.storage.url(file.name)
