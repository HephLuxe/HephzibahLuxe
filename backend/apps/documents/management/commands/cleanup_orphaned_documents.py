"""
Sweep up orphaned document artifacts. Two independent passes:

1. **Registry-row orphans** (`apps.documents.Document`): rows whose generic-FK
   source object no longer exists. Django can't cascade a generic FK, so before
   the post_delete signal in apps/meetings/signals.py existed, deleting a prep
   upload left a dangling Document row still served by GET /documents/. Deletes
   the row and its file blob.

2. **document_hub file blobs with no owning row**: storage files under
   document_hub's own paths (ClientDocument / Invoice / Receipt / PortalDefaults)
   that no live row references. These accrue when a ClientDocument is deleted or
   its file replaced (Django's FileField never deletes the old blob), and from
   the rare rollback-after-seed case (the auto-seed signal writes the file before
   the surrounding transaction commits; a rollback drops the row but not the
   blob). Deletes the blob only.

The second pass is scoped by the exact document_hub path structure
(`portals/<id>/events/<event>/{documents,invoices,receipts}/…` and
`portal_defaults/…`), so it can never touch another app's files — e.g. budget
receipts live at `…/events/<event>/budget/receipts/…` and are excluded.

    python manage.py cleanup_orphaned_documents --dry-run   # report only
    python manage.py cleanup_orphaned_documents             # delete
"""

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from apps.document_hub.models import ClientDocument, Invoice, PortalDefaults, Receipt
from apps.documents.models import Document

# Leaf directory (path segment [4], under portals/<id>/events/<event>/) that each
# per-client document_hub file type lives in. Deliberately excludes "budget"
# (budget receipts are …/events/<event>/budget/receipts/, segment [4] == "budget").
_DOCUMENT_HUB_LEAVES = {"documents", "invoices", "receipts"}
_DEFAULTS_PREFIX = "portal_defaults"


class Command(BaseCommand):
    help = "Delete orphaned Document registry rows and orphaned document_hub file blobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be deleted without deleting anything.",
        )

    # ── Pass 1: Document registry-row orphans ────────────────────

    def _is_registry_orphan(self, doc: Document) -> bool:
        if doc.content_type_id is None or not doc.object_id:
            return False  # never linked to a source — not an orphan
        model = doc.content_type.model_class()
        if model is None:
            return True  # source model no longer exists in the codebase
        return not model.objects.filter(pk=doc.object_id).exists()

    def _sweep_registry_orphans(self, dry_run: bool) -> None:
        orphans = [doc for doc in Document.objects.select_related("content_type") if self._is_registry_orphan(doc)]

        if not orphans:
            self.stdout.write(self.style.SUCCESS("Registry rows: none orphaned."))
            return

        self.stdout.write(f"Registry rows: {len(orphans)} orphaned —")
        for doc in orphans:
            ct = doc.content_type.model if doc.content_type_id else "?"
            self.stdout.write(f"  Document id={doc.id}  source={ct}#{doc.object_id}  file_path={doc.file_path!r}")

        if dry_run:
            return

        blobs = 0
        for doc in orphans:
            if doc.file_path and default_storage.exists(doc.file_path):
                default_storage.delete(doc.file_path)
                blobs += 1
            doc.delete()
        self.stdout.write(self.style.SUCCESS(f"  → deleted {len(orphans)} row(s) and {blobs} blob(s)."))

    # ── Pass 2: document_hub file blobs with no owning row ───────

    def _is_document_hub_path(self, path: str) -> bool:
        parts = path.split("/")
        if parts and parts[0] == _DEFAULTS_PREFIX:
            return True
        return (
            len(parts) >= 5
            and parts[0] == "portals"
            and parts[2] == "events"
            and parts[4] in _DOCUMENT_HUB_LEAVES
        )

    def _referenced_paths(self) -> set[str]:
        referenced: set[str] = set()
        for model in (ClientDocument, Invoice, Receipt):
            referenced.update(name for name in model.objects.values_list("file", flat=True) if name)
        defaults = PortalDefaults.objects.first()
        if defaults:
            for field in (defaults.service_agreement_file, defaults.welcome_booklet_file, defaults.faq_file):
                if field:
                    referenced.add(field.name)
        return referenced

    def _iter_storage_files(self, prefix: str):
        try:
            dirs, files = default_storage.listdir(prefix)
        except (FileNotFoundError, NotADirectoryError):
            return  # directory doesn't exist yet — nothing to walk
        for name in files:
            yield f"{prefix}/{name}" if prefix else name
        for name in dirs:
            yield from self._iter_storage_files(f"{prefix}/{name}" if prefix else name)

    def _sweep_document_hub_blobs(self, dry_run: bool) -> None:
        referenced = self._referenced_paths()

        orphans = [
            path
            for root in ("portals", _DEFAULTS_PREFIX)
            for path in self._iter_storage_files(root)
            if self._is_document_hub_path(path) and path not in referenced
        ]

        if not orphans:
            self.stdout.write(self.style.SUCCESS("Document hub blobs: none orphaned."))
            return

        self.stdout.write(f"Document hub blobs: {len(orphans)} orphaned —")
        for path in orphans:
            self.stdout.write(f"  {path}")

        if dry_run:
            return

        for path in orphans:
            default_storage.delete(path)
        self.stdout.write(self.style.SUCCESS(f"  → deleted {len(orphans)} blob(s)."))

    # ── Entry point ──────────────────────────────────────────────

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        self._sweep_registry_orphans(dry_run)
        self._sweep_document_hub_blobs(dry_run)
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing deleted."))
