"""
apps/events/management/commands/rekey_public_image_paths.py

Moves gallery images (EventImage.image, the PUBLIC bucket) whose stored key no
longer matches what ``event_gallery_upload_path`` produces today.

    python manage.py rekey_public_image_paths [--limit N] [--commit]

Why: images uploaded before the path change were keyed
``portals/{portal}/events/{event_id}-{event.slug}/...``, and ``Event.slug`` is
built from the client's names, so the client's name is in every public
portfolio image URL. The current path drops the ``-{slug}`` suffix. For each
image the target is the current upload path for the same row and the same
basename (on prod, a UUID), so only the folder changes.

EventImage.image is the only public-bucket field whose path embeds the event
slug. TeamMember.photo is public too, but its path is ``team_photos/{id}/``.

Dry run (the default)
---------------------
Prints the count and every ``old -> new`` mapping, plus what storage holds at
each end. Writes nothing: the run is inside a transaction that is marked READ
ONLY on Postgres and rolled back, and storage is only read (exists/size).

--commit
--------
1. Copy each object to its new key: a server-side copy_object when the storage
   is django-storages S3 (R2), else read the old object and save it under the
   exact new name. A target that already exists with the same size counts as
   copied (a re-run after an interrupted commit). A target with a different
   size aborts the run.
2. One DB transaction updates every row's file name. A row whose name changed
   in the meantime aborts and rolls back all of them.
3. Only after that commit, delete the old objects. A failed delete is logged
   and reported, not raised: the rows already point at the new keys, so a
   leftover old object costs storage, not correctness. An old key that some
   row still references is never deleted.
4. Drop the portfolio cache: the list and each affected public slug.

An abort in step 1 or 2 leaves every row and every old object as it was;
copies already made are left in place, and the re-run counts them as copied.

Rows are updated with ``queryset.update()``, so no save() signals fire: no
notification, email or blob-cleanup receiver runs. The cache is dropped
explicitly in step 4 instead.

Images whose path can't be computed (the event has no celebrant portal, so
the path would say ``portals/unknown/``) are reported and skipped.

Idempotent: once moved, a row's name equals its current upload path, so a
second run finds 0.
"""

import logging
import posixpath
from dataclasses import dataclass

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.events.models import EventImage
from apps.events.public_views import invalidate_portfolio_cache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Move:
    pk: object
    old: str
    new: str
    public_slug: str | None


class RekeyAborted(CommandError):
    """Raised before the DB is touched: rows and old objects are unchanged."""


def _storage():
    return EventImage._meta.get_field("image").storage


def plan_moves(limit: int | None = None) -> tuple[list[Move], list[str]]:
    """(moves, skipped). Reads the DB only."""
    field = EventImage._meta.get_field("image")
    moves: list[Move] = []
    skipped: list[str] = []
    qs = (
        EventImage.objects.select_related("event__celebrant__portal")
        .exclude(image="")
        .order_by("created_at", "pk")
    )
    for img in qs.iterator():
        old = img.image.name
        new = field.generate_filename(img, posixpath.basename(old))
        if new == old:
            continue
        if new.startswith("portals/unknown/"):
            skipped.append(f"{img.pk}: {old} (event has no celebrant portal; target path unknown)")
            continue
        moves.append(Move(img.pk, old, new, img.event.public_slug))
        if limit and len(moves) >= limit:
            break

    targets = [m.new for m in moves]
    if len(set(targets)) != len(targets):
        raise RekeyAborted("Two images map to the same new key; refusing to continue.")
    return moves, skipped


def _server_side_copy(storage):
    """A copy function for django-storages S3 storages, else None."""
    if not all(hasattr(storage, a) for a in ("bucket_name", "connection", "_normalize_name")):
        return None
    from storages.utils import clean_name

    def copy(old: str, new: str) -> None:
        storage.connection.meta.client.copy_object(
            Bucket=storage.bucket_name,
            Key=storage._normalize_name(clean_name(new)),
            CopySource={
                "Bucket": storage.bucket_name,
                "Key": storage._normalize_name(clean_name(old)),
            },
        )

    return copy


def _read_and_save(storage, old: str, new: str) -> None:
    with storage.open(old, "rb") as fh:
        data = fh.read()
    saved = storage.save(new, ContentFile(data))
    if saved != new:
        # The storage picked another name (the target appeared since we
        # checked). Never leave a suffixed copy behind or point a row at it.
        storage.delete(saved)
        raise RekeyAborted(f"Storage saved {new} as {saved}; aborting.")


def target_state(storage, move: Move) -> str:
    """'copy', 'present' (same size), or raises RekeyAborted. Reads only."""
    if not storage.exists(move.old):
        raise RekeyAborted(f"Source object missing: {move.old}")
    if not storage.exists(move.new):
        return "copy"
    old_size, new_size = storage.size(move.old), storage.size(move.new)
    if old_size != new_size:
        raise RekeyAborted(
            f"Target exists with a different size ({new_size} vs {old_size} bytes): {move.new}"
        )
    return "present"


def copy_object(storage, move: Move) -> str:
    """Make ``move.new`` hold ``move.old``'s bytes. Returns 'copied' or 'present'."""
    if target_state(storage, move) == "present":
        return "present"
    copy = _server_side_copy(storage) or (lambda old, new: _read_and_save(storage, old, new))
    copy(move.old, move.new)
    if storage.size(move.new) != storage.size(move.old):
        raise RekeyAborted(f"Copy of {move.old} to {move.new} has the wrong size.")
    return "copied"


class Command(BaseCommand):
    help = (
        "Move public gallery images (EventImage) to their current upload path, which no "
        "longer embeds the event slug. Dry run unless --commit."
    )

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true", help="Apply. Without it nothing is written.")
        parser.add_argument("--limit", type=int, default=None, help="Move at most N images this run.")

    def handle(self, *args, **options):
        commit = options["commit"]
        limit = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit must be a positive integer.")

        self.stdout.write(
            f"rekey_public_image_paths — {'COMMIT' if commit else 'DRY RUN (nothing will be written)'}"
        )
        if commit:
            self._commit(limit)
        else:
            self._dry_run(limit)

    # ── dry run ──────────────────────────────────────────────────────────────

    def _dry_run(self, limit):
        storage = _storage()
        with transaction.atomic():
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
            moves, skipped = plan_moves(limit)
            self._print_plan(moves, skipped)
            for move in moves:
                try:
                    state = target_state(storage, move)
                    note = "target already present, same size" if state == "present" else "will copy"
                except RekeyAborted as exc:
                    note = f"WILL ABORT: {exc}"
                self.stdout.write(f"  {move.old} -> {move.new}  [{note}]")
            transaction.set_rollback(True)
        self.stdout.write("Dry run: nothing written, nothing copied or deleted. Re-run with --commit to apply.")

    def _print_plan(self, moves, skipped):
        self.stdout.write(f"{len(moves)} image(s) to re-key.")
        for line in skipped:
            self.stdout.write(f"  SKIP {line}")

    # ── commit ───────────────────────────────────────────────────────────────

    def _commit(self, limit):
        storage = _storage()
        moves, skipped = plan_moves(limit)
        self._print_plan(moves, skipped)
        if not moves:
            self.stdout.write("Nothing to do.")
            return

        # 1. Copy. Nothing in the DB has changed yet, and no old object is
        # deleted until step 3, so any failure here is safe to re-run.
        for move in moves:
            try:
                result = copy_object(storage, move)
            except Exception as exc:
                raise CommandError(
                    f"Copy failed for image {move.pk} ({move.old} -> {move.new}): {exc}. "
                    "No database row was changed and no old object was deleted; "
                    "copies already made are left in place and a re-run treats them as copied."
                ) from exc
            self.stdout.write(f"  {result}: {move.old} -> {move.new}")

        # 2. Point every row at its new key, all or nothing.
        with transaction.atomic():
            for move in moves:
                updated = EventImage.objects.filter(pk=move.pk, image=move.old).update(image=move.new)
                if updated != 1:
                    raise CommandError(
                        f"Image {move.pk} no longer has the name {move.old}; rolled back. "
                        "No row was changed and no old object was deleted. Re-run to re-plan."
                    )
        self.stdout.write(f"Updated {len(moves)} row(s).")

        # 3. Delete the old objects, now that no row points at them.
        failed = 0
        for move in moves:
            if EventImage.objects.filter(image=move.old).exists():
                self.stdout.write(f"  kept {move.old} (still referenced by another image)")
                continue
            try:
                storage.delete(move.old)
            except Exception:
                failed += 1
                logger.warning("Failed to delete old image object %s", move.old, exc_info=True)
                self.stderr.write(f"  could not delete {move.old} (left in storage)")
        if failed:
            self.stderr.write(f"{failed} old object(s) could not be deleted; see the log.")

        # 4. Portfolio cache: the list plus every affected public page.
        invalidate_portfolio_cache(*{m.public_slug for m in moves})
        self.stdout.write(self.style.SUCCESS(f"Re-keyed {len(moves)} image(s)."))
