"""
apps/events/management/commands/rekey_public_image_paths.py

Moves gallery images (EventImage.image, the PUBLIC bucket) whose stored key no
longer matches what ``event_gallery_upload_path`` produces today, and later
purges the objects left at the old keys.

    python manage.py rekey_public_image_paths [--limit N] [--commit]
    python manage.py rekey_public_image_paths --purge-old [--limit N] [--commit]

Why: images uploaded before the path change were keyed
``portals/{portal}/events/{event_id}-{event.slug}/...``, and ``Event.slug`` is
built from the client's names, so the client's name is in every public
portfolio image URL. The current path drops the ``-{slug}`` suffix. For each
image the target is the current upload path for the same row and the same
basename (on prod, a UUID), so only the folder changes.

EventImage.image is the only public-bucket field whose path embeds the event
slug. TeamMember.photo is public too, but its path is ``team_photos/{id}/``.

The flow is two separate runs, days apart
-----------------------------------------
1. Copy each old object to its new key.
2. Point the rows at the new keys (one transaction).
3. KEEP the old objects for a grace period. Cached API responses, the CDN,
   browsers and the frontend's static build still hold the old URLs; deleting
   at once would turn every one of those into a broken image.
4. Later, purge the old objects with ``--purge-old``.

Re-key: dry run (the default)
-----------------------------
Prints the count and every ``old -> new`` mapping, plus what storage holds at
each end. Writes nothing: the run is inside a transaction that is marked READ
ONLY on Postgres and rolled back, and storage is only read.

Re-key: --commit
----------------
1. Copy each object to its new key: a server-side copy_object when the storage
   is django-storages S3 (R2), else read the old object and save it under the
   exact new name. Every copy is SHA-256 verified against its source. A target
   that already holds the same bytes counts as present (a re-run after an
   interrupted commit).
2. One DB transaction updates every row's file name. A row whose name changed
   in the meantime aborts and rolls back all of them.
3. Old objects are NOT deleted (see above).
4. Drop the portfolio cache: the list and each affected public slug.

A target that already exists with DIFFERENT bytes is a conflict: that row is
left on its old key, every conflict is reported on stderr, and the command
exits non-zero after the other rows have been moved. The dry run reports the
same conflicts the same way, so they are visible before --commit.

An abort in step 1 or 2 leaves every row and every old object as it was;
copies already made are left in place, and the re-run counts them as present.

Rows are updated with ``queryset.update()``, so no save() signals fire: no
notification, email or blob-cleanup receiver runs. The cache is dropped
explicitly in step 4 instead.

Images whose path can't be computed (the event has no celebrant portal, so
the path would say ``portals/unknown/``) are reported and skipped.

Idempotent: once moved, a row's name equals its current upload path, so a
second run finds 0.

Purge: --purge-old [--commit]
-----------------------------
For every image whose key is already in the current format
(``portals/{portal}/events/{event_id}/...``), re-derive the legacy key
deterministically by putting the event's internal slug back:
``portals/{portal}/events/{event_id}-{event.slug}/...``. The legacy object is
deleted only when BOTH the current key exists in storage and the legacy object
exists. Without --commit it only reports what it would delete (DB read-only,
storage only probed with exists()). Counts are printed either way. Idempotent:
a second run finds no legacy objects.

The legacy key is derived from the event's slug as it is NOW. If the event was
renamed after its images were uploaded, the derived key won't exist and that
object is reported as "legacy not found" rather than guessed at.
"""

import hashlib
import posixpath
from contextlib import contextmanager
from dataclasses import dataclass

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.events.models import EventImage
from apps.events.public_views import invalidate_portfolio_cache


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


def content_digest(storage, name):
    digest = hashlib.sha256()
    with storage.open(name, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def target_state(storage, move: Move) -> str:
    """Return copy, present, or conflict after comparing actual content."""
    if not storage.exists(move.old):
        raise RekeyAborted(f"Source object missing: {move.old}")
    if not storage.exists(move.new):
        return "copy"
    if content_digest(storage, move.old) != content_digest(storage, move.new):
        return "conflict"
    return "present"


def copy_object(storage, move: Move) -> str:
    state = target_state(storage, move)
    if state != "copy":
        return state
    expected = content_digest(storage, move.old)
    copy = _server_side_copy(storage) or (lambda old, new: _read_and_save(storage, old, new))
    copy(move.old, move.new)
    if content_digest(storage, move.new) != expected:
        raise RekeyAborted(f"Copy of {move.old} to {move.new} has the wrong content.")
    return "copied"




# ── purge (--purge-old) ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Purge:
    pk: object
    current: str
    legacy: str


def legacy_key(current: str, event_id, internal_slug: str) -> str | None:
    """The pre-change key for ``current``, or None if ``current`` is not in the
    current format for this event (not re-keyed yet, or some other layout).

    ``portals/{portal}/events/{event_id}/rest`` ->
    ``portals/{portal}/events/{event_id}-{internal_slug}/rest``
    """
    parts = current.split("/", 4)
    if (
        len(parts) != 5 or parts[0] != "portals" or parts[2] != "events"
        or parts[3] != str(event_id) or not internal_slug
    ):
        return None
    return "/".join([*parts[:3], f"{event_id}-{internal_slug}", parts[4]])


def plan_purge(storage, limit: int | None = None) -> tuple[list[Purge], dict[str, list[str]]]:
    """(to_delete, report). Reads the DB and probes storage with exists() only."""
    report = {"not_rekeyed": [], "legacy_absent": [], "current_missing": [], "still_referenced": []}
    purges: list[Purge] = []
    qs = (
        EventImage.objects.select_related("event")
        .exclude(image="")
        .order_by("created_at", "pk")
    )
    for img in qs.iterator():
        current = img.image.name
        legacy = legacy_key(current, img.event_id, img.event.slug)
        if legacy is None:
            report["not_rekeyed"].append(f"{img.pk}: {current}")
            continue
        if not storage.exists(legacy):
            report["legacy_absent"].append(f"{img.pk}: {legacy}")
            continue
        if not storage.exists(current):
            report["current_missing"].append(f"{img.pk}: {current} (legacy {legacy} kept)")
            continue
        if EventImage.objects.filter(image=legacy).exists():
            report["still_referenced"].append(f"{img.pk}: {legacy}")
            continue
        purges.append(Purge(img.pk, current, legacy))
        if limit and len(purges) >= limit:
            break
    return purges, report


@contextmanager
def read_only_transaction():
    """A transaction marked READ ONLY on Postgres and always rolled back."""
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
        yield
        transaction.set_rollback(True)


class Command(BaseCommand):
    help = (
        "Move public gallery images (EventImage) to their current upload path, which no "
        "longer embeds the event slug, keeping the old objects. --purge-old deletes those "
        "old objects later. Dry run unless --commit."
    )

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true", help="Apply. Without it nothing is written.")
        parser.add_argument("--limit", type=int, default=None, help="Handle at most N images this run.")
        parser.add_argument(
            "--purge-old", action="store_true",
            help="Instead of re-keying: delete legacy (slug-bearing) objects whose re-keyed "
                 "copy exists. Dry run unless --commit.",
        )

    def handle(self, *args, **options):
        commit = options["commit"]
        limit = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit must be a positive integer.")

        mode = "purge old objects" if options["purge_old"] else "re-key"
        self.stdout.write(
            f"rekey_public_image_paths ({mode}) — "
            f"{'COMMIT' if commit else 'DRY RUN (nothing will be written)'}"
        )
        if options["purge_old"]:
            self._purge(limit, commit)
        elif commit:
            self._commit(limit)
        else:
            self._dry_run(limit)

    def _fail_on_conflicts(self, conflicts):
        if not conflicts:
            return
        for move in conflicts:
            self.stderr.write(
                f"CONFLICT image {move.pk}: {move.new} already exists with different content "
                f"than {move.old}; row left on its old key."
            )
        raise CommandError(
            f"{len(conflicts)} conflicting destination(s); see stderr. Resolve them "
            "(inspect both objects in the bucket) and re-run."
        )

    # ── re-key: dry run ──────────────────────────────────────────────────────

    def _dry_run(self, limit):
        storage = _storage()
        conflicts = []
        with read_only_transaction():
            moves, skipped = plan_moves(limit)
            self._print_plan(moves, skipped)
            for move in moves:
                try:
                    state = target_state(storage, move)
                    note = {
                        "present": "target already present, same SHA-256",
                        "copy": "will copy",
                        "conflict": "CONFLICT: target exists with different content",
                    }[state]
                    if state == "conflict":
                        conflicts.append(move)
                except RekeyAborted as exc:
                    note = f"WILL ABORT: {exc}"
                self.stdout.write(f"  {move.old} -> {move.new}  [{note}]")
        self.stdout.write("Dry run: nothing written, nothing copied or deleted. Re-run with --commit to apply.")
        self._fail_on_conflicts(conflicts)

    def _print_plan(self, moves, skipped):
        self.stdout.write(f"{len(moves)} image(s) to re-key.")
        for line in skipped:
            self.stdout.write(f"  SKIP {line}")

    # ── re-key: commit ───────────────────────────────────────────────────────

    def _commit(self, limit):
        storage = _storage()
        moves, skipped = plan_moves(limit)
        self._print_plan(moves, skipped)
        if not moves:
            self.stdout.write("Nothing to do.")
            return

        # 1. Copy. Nothing in the DB has changed yet and nothing is ever deleted
        # here, so any failure is safe to re-run.
        copied, conflicts = [], []
        for move in moves:
            try:
                result = copy_object(storage, move)
            except Exception as exc:
                raise CommandError(
                    f"Copy failed for image {move.pk} ({move.old} -> {move.new}): {exc}. "
                    "No database row was changed and no old object was deleted; "
                    "copies already made are left in place and a re-run treats them as present."
                ) from exc
            self.stdout.write(f"  {result}: {move.old} -> {move.new}")
            (conflicts if result == "conflict" else copied).append(move)

        # 2. Point every non-conflicting row at its new key, all or nothing.
        with transaction.atomic():
            for move in copied:
                updated = EventImage.objects.filter(pk=move.pk, image=move.old).update(image=move.new)
                if updated != 1:
                    raise CommandError(
                        f"Image {move.pk} no longer has the name {move.old}; rolled back. "
                        "No row was changed and no old object was deleted. Re-run to re-plan."
                    )
        self.stdout.write(f"Updated {len(copied)} row(s).")

        # 3. Old objects are kept for the grace period; --purge-old removes them.
        self.stdout.write(
            "Old objects retained for cached public URLs. Delete them later with --purge-old."
        )

        # 4. Portfolio cache: the list plus every affected public page.
        invalidate_portfolio_cache(*{m.public_slug for m in copied})
        self.stdout.write(self.style.SUCCESS(f"Re-keyed {len(copied)} image(s)."))
        self._fail_on_conflicts(conflicts)

    # ── purge ────────────────────────────────────────────────────────────────

    def _purge(self, limit, commit):
        storage = _storage()
        with read_only_transaction():
            purges, report = plan_purge(storage, limit)

        self.stdout.write(f"{len(purges)} legacy object(s) to delete (current key present).")
        for p in purges:
            self.stdout.write(f"  {'delete' if commit else 'would delete'} {p.legacy}  (current: {p.current})")
        for line in report["current_missing"]:
            self.stderr.write(f"  KEEP current key missing from storage: {line}")
        for line in report["still_referenced"]:
            self.stderr.write(f"  KEEP legacy key still referenced by a row: {line}")
        self.stdout.write(
            "Counts: "
            f"to_delete={len(purges)} "
            f"legacy_not_found={len(report['legacy_absent'])} "
            f"not_rekeyed_yet={len(report['not_rekeyed'])} "
            f"current_missing={len(report['current_missing'])} "
            f"still_referenced={len(report['still_referenced'])}"
        )
        if not commit:
            self.stdout.write("Dry run: nothing deleted. Re-run with --purge-old --commit to delete.")
            return

        deleted, failed = 0, []
        for p in purges:
            try:
                storage.delete(p.legacy)
            except Exception as exc:
                failed.append(f"{p.legacy}: {exc}")
                continue
            deleted += 1
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} legacy object(s)."))
        if failed:
            for line in failed:
                self.stderr.write(f"  could not delete {line}")
            raise CommandError(f"{len(failed)} legacy object(s) could not be deleted; re-run to retry.")
