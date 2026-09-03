"""
apps/core/admin_files.py

Staff-side download links for the private storage tier, and the admin view
behind them.

Why this is a separate view and not a link to ``/api/v1/files/<type>/<id>/``
---------------------------------------------------------------------------
Two reasons, either one sufficient:

* **The API endpoint would reject the admin.** DRF is configured with
  ``DEFAULT_AUTHENTICATION_CLASSES = (JWTAuthentication,)`` only, so a staff
  member authenticated by the admin's *session cookie* is anonymous to it. The
  link would 401.
* **It returns JSON, not a file.** That is the right shape for a frontend that
  will put the URL in an ``<img>``; it is the wrong shape for a person clicking
  "Download" in a changelist.

So this is a plain Django view under ``staff_member_required`` (session auth,
redirects to the admin login like every other admin URL) that 302s to a freshly
minted URL. It reuses the same ``FILE_TYPES`` registry as the API, so a file
field registered once is reachable from both.

Why a redirect rather than rendering the signed URL into the page
----------------------------------------------------------------
Rendering it inline would embed a 60-second signature in the HTML, so every link
on the page would be dead by the time anyone read the page. Redirecting means
the signature is minted at the moment of the click and the link in the page
never expires — the same reasoning as the API endpoint, see
apps/core/filelinks.py.

Note the deliberately weaker permission check: ``staff_member_required`` and
nothing else. The API endpoint scopes clients to their own portal; this view is
reachable only by ``is_staff``, and staff already pass ``_may_read`` for every
portal there. Adding a portal check here would refuse nobody it does not
already admit.
"""

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, HttpResponseRedirect
from django.utils.html import format_html

from apps.core.filelinks import mint_url, spec_for_type


@staff_member_required
def admin_private_file(request, file_type: str, obj_id: str):
    """Redirect a staff click to a freshly signed URL for one private file."""
    spec = spec_for_type(file_type)
    if spec is None:
        raise Http404(f"Unknown file type '{file_type}'.")

    model = spec.get_model()
    try:
        instance = model.objects.get(pk=obj_id)
    except (model.DoesNotExist, ValueError, TypeError):
        raise Http404(f"No such {spec.label}.") from None

    url = mint_url(instance, spec)
    if url is None:
        raise Http404(f"This {spec.label} has no file attached.")
    return HttpResponseRedirect(url)


def admin_file_path(file_type: str, obj_id) -> str:
    """The staff download path. Mirrors filelinks.file_url_path, different route."""
    return f"/admin-files/{file_type}/{obj_id}/"


class PrivateFileAdminMixin:
    """
    Adds ``file_link`` (and ``file_preview`` for images) to a ModelAdmin or
    inline whose model holds a private file.

    Set ``private_file_type`` to the registry slug, then put ``"file_link"`` in
    ``list_display`` and/or ``readonly_fields``. Both are read-only displays, so
    the writable ``file`` field is unaffected — uploads still work exactly as
    before.
    """

    private_file_type: str = ""

    def _file_field(self, obj):
        spec = spec_for_type(self.private_file_type)
        if spec is None:
            return None, None
        return spec, getattr(obj, spec.field, None)

    @admin.display(description="File")
    def file_link(self, obj):
        spec, file = self._file_field(obj)
        if not file:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">Download {}</a>',
            admin_file_path(self.private_file_type, obj.pk),
            spec.label,
        )

    @admin.display(description="Preview")
    def file_preview(self, obj):
        """
        For image fields. The ``<img src>`` points at the redirect, so the
        browser follows it and fetches a URL signed at request time — the
        preview cannot be stale, however long the page has been open.
        """
        _spec, file = self._file_field(obj)
        if not file:
            return "—"
        return format_html(
            '<img src="{}" style="max-height: 60px;" alt="" />',
            admin_file_path(self.private_file_type, obj.pk),
        )
