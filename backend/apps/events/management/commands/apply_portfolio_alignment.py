"""
apps/events/management/commands/apply_portfolio_alignment.py

Brings the three real portfolio events on prod into line with the static site,
from a checked-in plan (apps/events/data/portfolio_alignment.json).

    python manage.py apply_portfolio_alignment \\
        [--plan apps/events/data/portfolio_alignment.json] \\
        [--images-root ../frontend/public] [--commit]

The plan
--------
It records, per object, the state prod is expected to be in now ("before") and
the state to leave it in ("after"):

  events   id, identity (headline, event_type, is_published),
           sha256 of the internal slug (the slug is the client's name, so the
           repo carries only its hash), public_slug before/after.
  days     id, identity (owner, headline, date), slug + event_day_title
           before/after.
  images   every existing gallery row: id, event, day, sha256 of its storage key
           (two hashes: the legacy slug-bearing key and the re-keyed one, so the
           plan holds whether or not rekey_public_image_paths has run), and
           sort_order / is_primary / is_published before/after.
  uploads  new rows: a fixed id, event, day, file name under --images-root,
           its sha256, sort_order / is_primary / is_published.

Dry run (the default)
---------------------
Read-only: the DB work runs in a transaction marked READ ONLY on Postgres and
rolled back, and storage is only read. It first checks that prod matches the
plan's "before" state EXACTLY — every event, day and image id, the set of
days and images on each event, every sort order, primary and published flag,
every identity field and key hash. Any difference is printed on stderr and the
command exits non-zero: the plan was computed against a state that no longer
exists, and must be recomputed rather than forced. If instead prod already
matches the "after" state, it says "already aligned" and exits 0. Then it
prints the whole plan: public slug and day label changes, uploads (with each
local file's sha256 checked against the plan), and every image whose order,
cover status or publication changes.

--commit
--------
1. The same state check; refuses on any mismatch, stops if already aligned.
2. Uploads, before any DB write: each file is saved through the EventImage
   field's own storage at the key its row WILL have (the current, slug-free
   upload path; the id is fixed by the plan, so the key is too), and its
   stored bytes are SHA-256 verified. A key that already holds the same bytes
   (a re-run after a failed commit) is reused; different bytes abort.
3. ONE transaction: re-checks the state under row locks, then clears every
   primary that is going away, sets sort orders and publication, inserts the
   upload rows (with their primaries), sets the primaries that are new, the
   public slugs, and the day titles/slugs. Clearing first means no
   intermediate state ever has two primaries in one gallery, so the partial
   unique constraints hold at every statement.
4. If that transaction fails, nothing in the DB changed, and the uploaded
   objects are orphans: their keys are printed on stderr. Re-running reuses
   them.
5. The portfolio cache is dropped for the list and every old and new public
   slug.

Rows are written with ``queryset.update()`` / ``bulk_create()``, so no model
signals fire: no notification, no email, no engagement. Idempotent: after a
commit, the state check recognises the "after" state and does nothing.
"""

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.events.models import Event, EventDay, EventImage
from apps.events.public_views import invalidate_portfolio_cache

DEFAULT_PLAN = Path(__file__).resolve().parents[2] / "data" / "portfolio_alignment.json"

IMAGE_FLAGS = ("sort_order", "is_primary", "is_published")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_stored(storage, name: str) -> str:
    digest = hashlib.sha256()
    with storage.open(name, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_plan(path) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            plan = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise CommandError(f"Cannot read plan {path}: {exc}")
    if not isinstance(plan, dict) or plan.get("version") != 1:
        raise CommandError(f"{path}: expected a version 1 alignment plan.")
    for key in ("events", "days", "images", "uploads"):
        if not isinstance(plan.get(key), list):
            raise CommandError(f"{path}: missing list {key!r}.")
    ids = [str(x["id"]) for x in plan["images"] + plan["uploads"]]
    if len(ids) != len(set(ids)):
        raise CommandError(f"{path}: an image id appears twice.")
    return plan


@contextmanager
def read_only_transaction():
    """A transaction marked READ ONLY on Postgres and always rolled back."""
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
        yield
        transaction.set_rollback(True)


def _image_field():
    return EventImage._meta.get_field("image")


def upload_key(upload: dict, event: Event) -> str:
    """The storage key the upload's row will have: the field's own upload path
    for an unsaved row with the plan's fixed id."""
    row = EventImage(
        id=upload["id"], event=event, event_day_id=upload["event_day_id"],
    )
    return _image_field().generate_filename(row, upload["filename"])


@dataclass
class Inspection:
    mismatches: list[str] = field(default_factory=list)
    # Elements whose before and after differ, by which of the two they match.
    at_before: list[str] = field(default_factory=list)
    at_after: list[str] = field(default_factory=list)
    events: dict = field(default_factory=dict)
    upload_keys: dict = field(default_factory=dict)

    @property
    def state(self) -> str:
        if self.mismatches:
            return "mismatch"
        if self.at_before and self.at_after:
            return "mixed"
        return "aligned" if not self.at_before else "before"


def _side(current: dict, before: dict, after: dict) -> str | None:
    """'before', 'after', 'both' (they are equal), or None (neither)."""
    is_before, is_after = current == before, current == after
    if is_before and is_after:
        return "both"
    return "before" if is_before else "after" if is_after else None


def _classify(result: Inspection, label: str, current: dict, before: dict, after: dict):
    side = _side(current, before, after)
    if side is None:
        result.mismatches.append(f"{label}: is {current}, plan expects {before} (before) or {after} (after)")
    elif side == "before":
        result.at_before.append(label)
    elif side == "after":
        result.at_after.append(label)


def inspect(plan: dict, *, lock: bool = False) -> Inspection:
    """Compare the DB to the plan. Reads only (``lock`` takes row locks)."""
    result = Inspection()
    event_ids = [e["id"] for e in plan["events"]]

    events_qs = Event.objects.filter(pk__in=event_ids).select_related("celebrant__portal")
    images_qs = EventImage.objects.filter(event_id__in=event_ids)
    days_qs = EventDay.objects.filter(owner_id__in=event_ids)
    if lock:
        events_qs = events_qs.select_for_update(of=("self",))
        images_qs = images_qs.select_for_update()
        days_qs = days_qs.select_for_update()
    events = {e.pk: e for e in events_qs}
    days = {str(d.pk): d for d in days_qs}
    images = {str(i.pk): i for i in images_qs}
    result.events = events

    # ── events ──
    for pe in plan["events"]:
        label = f"event {pe['id']}"
        event = events.get(pe["id"])
        if event is None:
            result.mismatches.append(f"{label}: does not exist")
            continue
        for name, want in pe["identity"].items():
            if getattr(event, name) != want:
                result.mismatches.append(f"{label}: {name} is {getattr(event, name)!r}, plan has {want!r}")
        if sha256_text(event.slug) != pe["internal_slug_sha256"]:
            result.mismatches.append(f"{label}: internal slug does not match the plan's hash")
        _classify(
            result, f"{label} public_slug", {"public_slug": event.public_slug},
            pe["before"], pe["after"],
        )
        target = pe["after"]["public_slug"]
        holder = Event.objects.filter(public_slug=target).exclude(pk=event.pk).first()
        if holder:
            result.mismatches.append(f"{label}: public_slug {target!r} is already held by event {holder.pk}")

    # ── days: exactly the plan's set, per event ──
    plan_days = {d["id"]: d for d in plan["days"]}
    for event_id in event_ids:
        want = {d["id"] for d in plan["days"] if d["identity"]["owner_id"] == event_id}
        have = {pk for pk, d in days.items() if d.owner_id == event_id}
        for pk in sorted(have - want):
            result.mismatches.append(f"event {event_id}: unexpected day {pk}")
        for pk in sorted(want - have):
            result.mismatches.append(f"event {event_id}: day {pk} is missing")
    for pk, pd in plan_days.items():
        day = days.get(pk)
        if day is None:
            continue
        label = f"day {pk}"
        identity = {"owner_id": day.owner_id, "headline": day.headline, "date": day.date.isoformat()}
        if identity != pd["identity"]:
            result.mismatches.append(f"{label}: identity is {identity}, plan has {pd['identity']}")
        _classify(
            result, label, {"slug": day.slug, "event_day_title": day.event_day_title},
            pd["before"], pd["after"],
        )

    # ── images: exactly the plan's set (+ the uploads once applied), per event ──
    uploads = {u["id"]: u for u in plan["uploads"]}
    for event_id in event_ids:
        existing = {i["id"] for i in plan["images"] if i["event_id"] == event_id}
        new = {u["id"] for u in plan["uploads"] if u["event_id"] == event_id}
        have = {pk for pk, i in images.items() if i.event_id == event_id}
        if have == existing:
            if new:
                result.at_before.append(f"event {event_id} uploads")
        elif have == existing | new:
            if new:
                result.at_after.append(f"event {event_id} uploads")
        else:
            for pk in sorted(have - existing - new):
                result.mismatches.append(f"event {event_id}: unexpected image {pk}")
            for pk in sorted(existing - have):
                result.mismatches.append(f"event {event_id}: image {pk} is missing")
            present = sorted(have & new)
            if present and len(present) != len(new):
                result.mismatches.append(
                    f"event {event_id}: {len(present)} of {len(new)} planned uploads exist as rows"
                )

    for pi in plan["images"]:
        img = images.get(pi["id"])
        if img is None:
            continue
        label = f"image {pi['id']} ({pi['filename']})"
        where = {"event_id": img.event_id, "event_day_id": str(img.event_day_id) if img.event_day_id else None}
        if where != {"event_id": pi["event_id"], "event_day_id": pi["event_day_id"]}:
            result.mismatches.append(f"{label}: belongs to {where}, plan has event {pi['event_id']} day {pi['event_day_id']}")
        if sha256_text(img.image.name) not in pi["key_sha256"]:
            result.mismatches.append(f"{label}: storage key does not match the plan's hashes")
        _classify(
            result, label, {k: getattr(img, k) for k in IMAGE_FLAGS},
            pi["before"], pi["after"],
        )

    for pk, up in uploads.items():
        event = events.get(up["event_id"])
        if event is not None:
            result.upload_keys[pk] = upload_key(up, event)
        img = images.get(pk)
        if img is None:
            continue
        label = f"uploaded image {pk} ({up['filename']})"
        current = {
            "event_id": img.event_id,
            "event_day_id": str(img.event_day_id) if img.event_day_id else None,
            **{k: getattr(img, k) for k in IMAGE_FLAGS},
            "key": img.image.name,
        }
        want = {
            "event_id": up["event_id"], "event_day_id": up["event_day_id"],
            **{k: up[k] for k in IMAGE_FLAGS},
            "key": result.upload_keys.get(pk),
        }
        if current != want:
            result.mismatches.append(f"{label}: is {current}, plan has {want}")

    if not result.mismatches and result.at_before and result.at_after:
        result.mismatches.append(
            "partially aligned: some objects match the plan's before state and some its after "
            f"state (after: {', '.join(result.at_after[:5])}{' …' if len(result.at_after) > 5 else ''})"
        )
    return result


class Command(BaseCommand):
    help = (
        "Align the real portfolio events with the static site from a checked-in plan: "
        "reorders, covers, uploads, public slugs, day labels, unpublish. Dry run unless --commit."
    )

    def add_arguments(self, parser):
        parser.add_argument("--plan", default=str(DEFAULT_PLAN), help="Alignment plan JSON.")
        parser.add_argument(
            "--images-root", default=str(Path(settings.BASE_DIR).parent / "frontend" / "public"),
            help="Directory searched (recursively) for each upload's file name.",
        )
        parser.add_argument("--commit", action="store_true", help="Apply. Without it nothing is written.")

    def handle(self, *args, **options):
        plan = load_plan(options["plan"])
        images_root = Path(options["images_root"]).resolve()
        commit = options["commit"]
        self.stdout.write(
            f"apply_portfolio_alignment — {'COMMIT' if commit else 'DRY RUN (nothing will be written)'}"
        )
        self.stdout.write(f"  plan: {options['plan']}")
        self.stdout.write(f"  images root: {images_root}")

        if commit:
            self._commit(plan, images_root)
        else:
            with read_only_transaction():
                result = self._check(plan)
                if result.state == "before":
                    self._print_plan(plan, result, images_root, check_storage=True)
            if result.state == "before":
                self.stdout.write("Dry run: nothing written, nothing uploaded. Re-run with --commit to apply.")

    # ── state check ──────────────────────────────────────────────────────────

    def _check(self, plan, *, lock=False) -> Inspection:
        result = inspect(plan, lock=lock)
        if result.mismatches:
            for line in result.mismatches:
                self.stderr.write(f"MISMATCH {line}")
            raise CommandError(
                f"Prod does not match the plan's before state ({len(result.mismatches)} "
                "difference(s), see stderr). Refusing to proceed; recompute the plan."
            )
        if result.state == "aligned":
            self.stdout.write(self.style.SUCCESS("Already aligned: prod matches the plan's after state. Nothing to do."))
        else:
            self.stdout.write("State check: prod matches the plan's before state exactly.")
        return result

    # ── local files ──────────────────────────────────────────────────────────

    def _locals(self, plan, images_root: Path) -> tuple[dict, list[str]]:
        """{upload id: local path} and a list of problems (missing / sha256)."""
        by_name: dict[str, list[Path]] = {}
        if images_root.is_dir():
            for path in images_root.rglob("*"):
                if path.is_file():
                    by_name.setdefault(path.name, []).append(path)
        found, problems = {}, []
        for up in plan["uploads"]:
            candidates = by_name.get(up["filename"], [])
            matching = [p for p in candidates if sha256_file(p) == up["sha256"]]
            if matching:
                found[up["id"]] = sorted(matching)[0]
            elif candidates:
                problems.append(f"{up['filename']}: found {len(candidates)} file(s), none with sha256 {up['sha256']}")
            else:
                problems.append(f"{up['filename']}: not found under {images_root}")
        return found, problems

    # ── output ───────────────────────────────────────────────────────────────

    def _print_plan(self, plan, result: Inspection, images_root: Path, *, check_storage: bool):
        w = self.stdout.write
        events = result.events
        locals_, problems = self._locals(plan, images_root)
        storage = _image_field().storage

        w("")
        w("PUBLIC SLUGS:")
        for pe in plan["events"]:
            if pe["before"] != pe["after"]:
                w(f"  event {pe['id']}: {pe['before']['public_slug']!r} -> {pe['after']['public_slug']!r}")
        w("DAY LABELS:")
        for pd in plan["days"]:
            if pd["before"] != pd["after"]:
                w(
                    f"  day {pd['id']} (event {pd['identity']['owner_id']}): "
                    f"title {pd['before']['event_day_title']!r} -> {pd['after']['event_day_title']!r}, "
                    f"slug {pd['before']['slug']!r} -> {pd['after']['slug']!r}"
                )

        w("UPLOADS:")
        for up in plan["uploads"]:
            where = f"day {up['event_day_id']}" if up["event_day_id"] else "event level"
            local = locals_.get(up["id"])
            check = f"sha256 OK ({local.relative_to(images_root)})" if local else "sha256 FAIL"
            key = result.upload_keys.get(up["id"], "?")
            note = ""
            if check_storage and storage.exists(key):
                same = local is not None and sha256_stored(storage, key) == up["sha256"]
                note = "  [key already in storage, same bytes: reused]" if same else "  [KEY IN STORAGE WITH OTHER BYTES]"
            w(
                f"  {up['filename']} -> event {up['event_id']} {where}, sort_order={up['sort_order']}"
                f"{', PRIMARY' if up['is_primary'] else ''}{'' if up['is_published'] else ', UNPUBLISHED'}"
                f"  {check}  key={key}{note}"
            )
        for line in problems:
            self.stderr.write(f"UPLOAD FILE {line}")

        w("COVERS (primary changes):")
        for pi in plan["images"]:
            if pi["before"]["is_primary"] != pi["after"]["is_primary"]:
                w(f"  {pi['filename']} ({pi['id']}): primary {pi['before']['is_primary']} -> {pi['after']['is_primary']}")
        for up in plan["uploads"]:
            if up["is_primary"]:
                where = f"day {up['event_day_id']}" if up["event_day_id"] else f"event {up['event_id']} event level"
                w(f"  {up['filename']} (upload): becomes primary of {where}")
        w("UNPUBLISH:")
        for pi in plan["images"]:
            if pi["before"]["is_published"] and not pi["after"]["is_published"]:
                w(f"  {pi['filename']} ({pi['id']})")
        w("REORDERS:")
        reorders = [pi for pi in plan["images"] if pi["before"]["sort_order"] != pi["after"]["sort_order"]]
        for pi in reorders:
            w(f"  {pi['filename']} ({pi['id']}): sort_order {pi['before']['sort_order']} -> {pi['after']['sort_order']}")
        w(
            f"Summary: {len(events)} event(s), {sum(pe['before'] != pe['after'] for pe in plan['events'])} "
            f"public slug change(s), {sum(pd['before'] != pd['after'] for pd in plan['days'])} day label change(s), "
            f"{len(plan['uploads'])} upload(s), {len(reorders)} reorder(s)."
        )
        if problems:
            raise CommandError(f"{len(problems)} upload file problem(s); see stderr. Refusing to proceed.")
        unknown = [k for k in result.upload_keys.values() if k.startswith("portals/unknown/")]
        if unknown:
            raise CommandError("An event has no celebrant portal, so its upload path is unknown. Refusing.")

    # ── commit ───────────────────────────────────────────────────────────────

    def _commit(self, plan, images_root: Path):
        result = self._check(plan)
        if result.state == "aligned":
            return
        self._print_plan(plan, result, images_root, check_storage=True)
        locals_, _ = self._locals(plan, images_root)

        # 1. Uploads, before any DB write.
        storage = _image_field().storage
        stored: list[str] = []
        for up in plan["uploads"]:
            key = result.upload_keys[up["id"]]
            try:
                self._store(storage, key, locals_[up["id"]], up["sha256"])
            except CommandError:
                self._report_orphans(stored)
                raise
            stored.append(key)
        self.stdout.write(f"Uploaded/verified {len(stored)} object(s).")

        # 2. Every DB change in one transaction.
        try:
            with transaction.atomic():
                locked = inspect(plan, lock=True)
                if locked.state != "before":
                    raise CommandError(
                        f"State changed since the check (now {locked.state!r}); rolled back."
                    )
                self._apply(plan, result.upload_keys)
        except Exception as exc:
            self._report_orphans(stored)
            raise CommandError(f"Database changes failed and were rolled back: {exc}") from exc

        # 3. Cache: the list plus every old and new public page.
        invalidate_portfolio_cache(
            *[pe["before"]["public_slug"] for pe in plan["events"]],
            *[pe["after"]["public_slug"] for pe in plan["events"]],
        )
        after = inspect(plan)
        if after.state != "aligned":
            raise CommandError(f"Committed, but the post-check says {after.state!r}: {after.mismatches[:5]}")
        self.stdout.write(self.style.SUCCESS("Committed. Prod now matches the plan's after state."))

    def _store(self, storage, key: str, local: Path, sha: str) -> None:
        if storage.exists(key):
            if sha256_stored(storage, key) != sha:
                raise CommandError(f"{key} already exists in storage with different bytes. Refusing.")
            self.stdout.write(f"  present {key}")
            return
        with open(local, "rb") as fh:
            saved = storage.save(key, File(fh, name=local.name))
        if saved != key:
            storage.delete(saved)
            raise CommandError(f"Storage saved {key} as {saved}; removed it. Refusing.")
        if sha256_stored(storage, key) != sha:
            raise CommandError(f"Stored {key} does not match sha256 {sha}.")
        self.stdout.write(f"  uploaded {key}")

    def _report_orphans(self, keys):
        if not keys:
            return
        self.stderr.write(
            f"{len(keys)} uploaded object(s) are not referenced by any row (a re-run reuses them):"
        )
        for key in keys:
            self.stderr.write(f"  ORPHAN {key}")

    def _apply(self, plan, upload_keys: dict) -> None:
        now = timezone.now()

        def changes(pi):
            return {k: pi["after"][k] for k in IMAGE_FLAGS if pi["before"][k] != pi["after"][k]}

        # a. Clear every primary that is going away, before anything gains one.
        losing = [pi["id"] for pi in plan["images"] if pi["before"]["is_primary"] and not pi["after"]["is_primary"]]
        EventImage.objects.filter(pk__in=losing).update(is_primary=False, updated_at=now)

        # b. Order and publication.
        for pi in plan["images"]:
            fields = {k: v for k, v in changes(pi).items() if k != "is_primary"}
            if fields:
                EventImage.objects.filter(pk=pi["id"]).update(**fields, updated_at=now)

        # c. The uploaded rows, primaries included (their galleries' old
        # primaries were cleared in a).
        EventImage.objects.bulk_create([
            EventImage(
                id=up["id"], event_id=up["event_id"], event_day_id=up["event_day_id"],
                image=upload_keys[up["id"]], alt_text="",
                **{k: up[k] for k in IMAGE_FLAGS},
            )
            for up in plan["uploads"]
        ])

        # d. Existing images that become primary.
        gaining = [pi["id"] for pi in plan["images"] if pi["after"]["is_primary"] and not pi["before"]["is_primary"]]
        EventImage.objects.filter(pk__in=gaining).update(is_primary=True, updated_at=now)

        # e. Public slugs: clear the changing ones first so two events could
        # swap without tripping the unique index.
        moving = [pe for pe in plan["events"] if pe["before"] != pe["after"]]
        Event.objects.filter(pk__in=[pe["id"] for pe in moving]).update(public_slug=None)
        for pe in moving:
            Event.objects.filter(pk=pe["id"]).update(public_slug=pe["after"]["public_slug"], updated_at=now)

        # f. Day labels, slugs cleared first for the same reason (unique per event).
        relabel = [pd for pd in plan["days"] if pd["before"] != pd["after"]]
        EventDay.objects.filter(pk__in=[pd["id"] for pd in relabel]).update(slug=None)
        for pd in relabel:
            EventDay.objects.filter(pk=pd["id"]).update(
                slug=pd["after"]["slug"], event_day_title=pd["after"]["event_day_title"], updated_at=now,
            )
