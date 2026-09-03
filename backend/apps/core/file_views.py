"""
apps/core/file_views.py

The one endpoint that serves every private file: ``GET /files/<type>/<id>/``.

It resolves the type against ``apps.core.filelinks.FILE_TYPES``, loads the
object, checks that the caller owns the portal it belongs to, and returns a
freshly signed URL that expires in 60 seconds. See filelinks.py for why this
exists at all and why it returns JSON rather than a 302.

One endpoint rather than six near-identical ones (documents, invoices, receipts,
budget receipts, prep uploads, contact photos): each of those would have needed
its own view, its own ownership check and its own tests, and the sixth would
inevitably have drifted from the first. Here the per-type part is a registry
entry and the security-critical part is written once.
"""

import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.error_codes import NOT_FOUND, VALIDATION_ERROR
from apps.core.filelinks import MINTED_URL_EXPIRY_SECONDS, mint_url, spec_for_type
from apps.core.permissions import is_staff_or_superuser

logger = logging.getLogger(__name__)


def _error(detail: str, code: str, http_status: int) -> Response:
    return Response({"detail": detail, "code": code}, status=http_status)


def _may_read(user, engagement) -> bool:
    """
    Staff read anything; a client reads only files under their own portal.

    A missing engagement is a refusal, not a pass. An event can exist without one
    (see FAILURE_POINTS_AUDIT F3/F7), and "we could not establish who owns this
    file" must never resolve to "anyone may have it".
    """
    if is_staff_or_superuser(user):
        return True
    if engagement is None:
        return False
    return engagement.portal.user_id == user.id


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mint_file_url(request, file_type: str, obj_id):
    """
    GET — mint a short-lived URL for one private file.

    Response: ``{"url": "...", "expires_in": 60}``

    404 is returned for an unknown type, a missing object, AND a file the caller
    may not read — deliberately the same response for all three. A 403 on the
    third would confirm that a given document id exists on some other client's
    portal, which is exactly the fact a caller enumerating ids is looking for.
    """
    spec = spec_for_type(file_type)
    if spec is None:
        # Fail closed. A file field that nobody registered is unreachable rather
        # than served without an ownership check.
        return _error(
            f"Unknown file type '{file_type}'.", VALIDATION_ERROR, status.HTTP_404_NOT_FOUND,
        )

    model = spec.get_model()
    try:
        instance = model.objects.get(pk=obj_id)
    except (model.DoesNotExist, ValueError, TypeError):
        # ValueError/TypeError: a malformed id for this model's pk type. A bad
        # id is "not found", not a 500.
        return _error(f"No such {spec.label}.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    if not _may_read(request.user, spec.engagement(instance)):
        logger.info(
            "Refused a private file read.",
            extra={
                "event": "private_file_read_refused",
                "file_type": file_type,
                "object_id": str(obj_id),
                "user_id": str(getattr(request.user, "pk", None)),
            },
        )
        return _error(f"No such {spec.label}.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    url = mint_url(instance, spec)
    if url is None:
        return _error(
            f"This {spec.label} has no file attached.", NOT_FOUND, status.HTTP_404_NOT_FOUND,
        )

    return Response({"url": url, "expires_in": MINTED_URL_EXPIRY_SECONDS})
