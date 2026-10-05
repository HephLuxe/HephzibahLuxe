"""
apps/events/management/commands/attach_static_portfolio.py

Maps one event of the static portfolio manifest (fixtures/static_portfolio.json)
onto an Event that ALREADY EXISTS — a real client's event — instead of creating
a new one the way import_static_portfolio does.

    python manage.py attach_static_portfolio --static-slug golden-50th \\
        --event <internal Event.slug> \\
        [--set-public-slug] [--day-slugs] [--cover] [--gallery] [--publish] [--commit]

Each flag is one step, and only the steps asked for run:

--set-public-slug  Event.public_slug := the static slug. Refused if another
                   event already holds it.
--day-slugs        EventDay.slug := the manifest sub-event's slug, for each
                   existing day matched to a sub-event (see "Day matching").
--cover            Upload the manifest cover as the event-level primary image —
                   only if the event has no event-level primary. If an
                   event-level image with the same file name is already there
                   it is promoted instead of uploading a copy.
--gallery          Fill EMPTY galleries only: the manifest's event-level
                   gallery goes to event level, each sub-event's images to its
                   matched day — but a target that already has ANY image is
                   skipped whole and reported ("SKIP day 'Event No. 1':
                   already has 14 images"). The emptiness test is the guard
                   that matters: images uploaded through the portal are stored
                   under their own names (on prod, UUIDs), so nothing in the
                   manifest can be recognised among them by name. File-name
                   dedupe (basename, case-insensitive, across all the event's
                   images) still runs as a second guard.
--force-gallery    Implies --gallery, without the emptiness test: uploads into
                   galleries that already have images, deduped by file name
                   only. Will duplicate photographs stored under other names;
                   the plan says so in capitals.
--publish          Event.is_published := True. Refused if the event would have
                   no public_slug (it would be published but unreachable).

Nothing else is touched: not Event.slug (it keys the portal), not title,
headline, description, dates, or any day's copy.

Day matching
------------
The event's days (ordered by date, start_time) are matched to the manifest's
sub-events in three passes, comparisons case/whitespace/dash-insensitive:

1. Headline: the day's headline equals the sub-event title, or the two agree up
   to the first " - " (so "Rooted in Gratitude" matches "Rooted in Gratitude —
   A Gathering of ..."). Headlines are distinctive; eyebrows are not.
2. Eyebrow / slug, for sub-events still unpaired, among days still unpaired:
   event_day_title equals the sub-event subtitle, or the day's slug already
   equals the sub-event slug.
3. If that leaves any sub-event unpaired and the counts are equal, days are
   paired by order — refused if any pairing from passes 1-2 points at a
   different position. Anything else is refused.

The mapping and how it was found are always printed; check them in the dry run.

Dry run (the default)
---------------------
Prints the exact plan and writes nothing: the run is inside a transaction that
is marked READ ONLY on Postgres and rolled back, and media storage is never
opened (local image files are only checked for existence). --commit applies
the plan in one transaction, publish last.

Side effects: only the events app's post_save receivers (portfolio cache
invalidation). No notification, email or engagement — those are queued by the
API views, which this command does not go through.

Idempotent: every step compares against the current state first, so a second
run with the same flags plans nothing.
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils.text import get_valid_filename

from apps.events import services
from apps.events.models import Event, EventDay, EventImage

from .import_static_portfolio import DEFAULT_MANIFEST, build_plan


def _norm(text) -> str:
    text = (text or "").replace("—", "-").replace("–", "-")
    return " ".join(text.casefold().split())


def _main_clause(normed: str) -> str:
    """'rooted in gratitude - a gathering of ...' -> 'rooted in gratitude'."""
    return normed.split(" - ", 1)[0].strip()


def _file_key(name: str) -> str:
    """Basename as storage would have saved it, case-folded. Storage passes
    every upload through get_valid_filename, so the manifest side is normalised
    the same way before comparing."""
    return get_valid_filename(Path(name).name).casefold()


def _n_images(n: int) -> str:
    return f"{n} image" if n == 1 else f"{n} images"


def match_days(days: list, subs: list[dict]):
    """
    Pair manifest sub-events with existing days. Returns
    ``(mapping, how)`` where mapping is ``{sub_index: EventDay}``, or
    ``(None, reason)`` when the pairing is ambiguous.
    """
    if not subs:
        return {}, "manifest has no sub-events"

    def headline_match(day, sub):
        d, s = _norm(day.headline), _norm(sub["headline"])
        return bool(d and s) and (d == s or _main_clause(d) == _main_clause(s))

    def label_match(day, sub):
        return bool(
            (_norm(day.event_day_title) and _norm(day.event_day_title) == _norm(sub["event_day_title"]))
            or (day.slug and sub["static_slug"] and day.slug == sub["static_slug"])
        )

    mapping, used = {}, set()
    for match in (headline_match, label_match):
        for i, sub in enumerate(subs):
            if i in mapping:
                continue
            found = [d for d in days if d.pk not in used and match(d, sub)]
            if len(found) == 1:
                mapping[i] = found[0]
                used.add(found[0].pk)

    if len(mapping) == len(subs):
        return mapping, "by title (headline first, then eyebrow/slug)"

    if len(days) == len(subs):
        conflicts = [i + 1 for i, day in sorted(mapping.items()) if days[i].pk != day.pk]
        if not conflicts:
            return {i: days[i] for i in range(len(subs))}, "by order (titles do not identify every day)"
        return None, (
            f"day order and titles disagree for sub-event(s) {conflicts}; "
            "fix the days' titles or order in the admin, then re-run"
        )

    return None, (
        f"the event has {len(days)} day(s) and the manifest {len(subs)} sub-event(s), "
        "and titles do not pair each sub-event with exactly one day"
    )


class Command(BaseCommand):
    help = (
        "Attach one static-portfolio manifest event to an EXISTING event: public slug, "
        "day slugs, cover, gallery, publish. Dry run unless --commit."
    )

    def add_arguments(self, parser):
        parser.add_argument("--static-slug", required=True, help="Manifest slug, e.g. golden-50th.")
        parser.add_argument("--event", required=True, help="Internal Event.slug of the existing event.")
        parser.add_argument("--set-public-slug", action="store_true", help="Set Event.public_slug to the static slug.")
        parser.add_argument("--day-slugs", action="store_true", help="Set EventDay.slug from the matched sub-events.")
        parser.add_argument("--cover", action="store_true", help="Upload the cover if the event has no event-level primary.")
        parser.add_argument(
            "--gallery", action="store_true",
            help="Upload manifest images into EMPTY galleries only (event level / each matched day).",
        )
        parser.add_argument(
            "--force-gallery", action="store_true",
            help="Implies --gallery, but also uploads into galleries that already have images "
                 "(file-name dedupe only — duplicates photos stored under other names).",
        )
        parser.add_argument("--publish", action="store_true", help="Set is_published=True.")
        parser.add_argument("--commit", action="store_true", help="Apply. Without it nothing is written.")
        parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
        parser.add_argument(
            "--images-root", default=str(Path(settings.BASE_DIR).parent / "frontend" / "public"),
            help="Directory the manifest's /images/... paths are relative to.",
        )

    # ── entry ────────────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        steps = {k: options[k] for k in ("set_public_slug", "day_slugs", "cover", "gallery", "publish")}
        steps["force_gallery"] = options["force_gallery"]
        steps["gallery"] = steps["gallery"] or steps["force_gallery"]
        if not any(steps.values()):
            raise CommandError(
                "Nothing to do: pass one or more of --set-public-slug, --day-slugs, "
                "--cover, --gallery, --publish."
            )

        item = self._manifest_item(options["manifest"], options["static_slug"])
        images_root = Path(options["images_root"]).resolve()
        commit = options["commit"]

        self.stdout.write(
            f"attach_static_portfolio — {'COMMIT' if commit else 'DRY RUN (nothing will be written)'}"
        )
        self.stdout.write(f"  static slug: {item['slug']}")
        self.stdout.write(f"  images root: {images_root}")

        if commit:
            with transaction.atomic():
                plan = self._plan(options["event"], item, steps, images_root)
                self._print(plan)
                self._apply(plan)
            self.stdout.write(self.style.SUCCESS("Committed."))
        else:
            with transaction.atomic():
                if connection.vendor == "postgresql":
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION READ ONLY")
                plan = self._plan(options["event"], item, steps, images_root)
                self._print(plan)
                transaction.set_rollback(True)
            self.stdout.write("Dry run: nothing written, nothing uploaded. Re-run with --commit to apply.")

    def _manifest_item(self, path, static_slug) -> dict:
        try:
            with open(path, encoding="utf-8") as fh:
                manifest = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Cannot read manifest {path}: {exc}")
        if not isinstance(manifest, list):
            raise CommandError("Manifest must be a JSON list of events.")
        by_slug = {p["slug"]: p for p in build_plan(manifest)}
        if static_slug not in by_slug:
            raise CommandError(
                f"No manifest event {static_slug!r}. Available: {', '.join(sorted(by_slug))}."
            )
        return by_slug[static_slug]

    # ── planning (reads only) ────────────────────────────────────────────────

    def _plan(self, event_slug, item, steps, images_root) -> dict:
        try:
            event = Event.objects.get(slug=event_slug)
        except Event.DoesNotExist:
            raise CommandError(f"No event with internal slug {event_slug!r}.")

        days = list(event.days.all())
        images = list(event.images.all())
        plan = {
            "event": event, "days": days, "images": images, "item": item,
            "public_slug": None, "day_slugs": [], "mapping": None, "mapping_how": "",
            "cover": None, "uploads": [], "publish": False, "notes": [], "skips": [],
            "force_gallery": steps["force_gallery"],
        }

        # 1. public slug
        final_public_slug = event.public_slug
        if steps["set_public_slug"]:
            target = item["slug"]
            if event.public_slug == target:
                plan["notes"].append(f"public_slug already {target!r} — no change")
            else:
                holder = Event.objects.filter(public_slug=target).exclude(pk=event.pk).first()
                if holder:
                    raise CommandError(
                        f"public_slug {target!r} is already used by event id {holder.pk} "
                        f"(internal slug {holder.slug!r}). Refusing."
                    )
                plan["public_slug"] = (event.public_slug, target)
            final_public_slug = target

        # 2. day mapping — needed for day slugs and for day-level gallery uploads
        needs_mapping = steps["day_slugs"] or (steps["gallery"] and item["days"])
        if needs_mapping:
            mapping, how = match_days(days, item["days"])
            if mapping is None:
                raise CommandError(f"Cannot map days to sub-events: {how}.")
            plan["mapping"], plan["mapping_how"] = mapping, how

        if steps["day_slugs"] and plan["mapping"]:
            mapped_pks = {d.pk for d in plan["mapping"].values()}
            for i, day in sorted(plan["mapping"].items()):
                target = item["days"][i]["static_slug"]
                if not target:
                    plan["notes"].append(f"sub-event {i + 1} has no slug in the manifest — day {day.pk} left alone")
                    continue
                clash = next((d for d in days if d.slug == target and d.pk not in mapped_pks), None)
                if clash:
                    raise CommandError(
                        f"day slug {target!r} is held by unmatched day {clash.pk} of this event. Refusing."
                    )
                if day.slug != target:
                    plan["day_slugs"].append((day, day.slug, target))
        elif steps["day_slugs"]:
            plan["notes"].append("day slugs: manifest has no sub-events — nothing to map")

        present = {_file_key(img.image.name) for img in images if img.image}

        # 3. cover
        if steps["cover"]:
            event_level = [img for img in images if img.event_day_id is None]
            primary = next((img for img in event_level if img.is_primary), None)
            cover_path = item["images"][0]
            if primary:
                plan["notes"].append(
                    f"cover: event already has an event-level primary ({Path(primary.image.name).name}) — skip"
                )
            else:
                same = next((img for img in event_level if _file_key(img.image.name) == _file_key(cover_path)), None)
                if same:
                    plan["cover"] = ("promote", same)
                else:
                    plan["cover"] = (
                        "upload", self._local(images_root, cover_path), cover_path,
                        services.next_sort_order(event, None),
                    )

        # 4. gallery
        if steps["gallery"]:
            force = steps["force_gallery"]
            headline = item["fields"]["headline"]
            # Counted from the CURRENT rows, before this run's cover upload:
            # "empty" means empty before we started.
            event_level_count = sum(1 for img in images if img.event_day_id is None)
            if item["images"][1:] and event_level_count and not force:
                plan["skips"].append(f"SKIP event level: already has {_n_images(event_level_count)}")
            elif item["images"][1:]:
                # After the cover when one is being uploaded, so the two never
                # share a sort_order.
                event_start = services.next_sort_order(event, None)
                if plan["cover"] and plan["cover"][0] == "upload":
                    event_start = plan["cover"][3] + 1
                self._plan_uploads(
                    plan, present, images_root,
                    paths=item["images"][1:], event_day=None,
                    has_primary=True,  # the event-level primary is --cover's job
                    alt_prefix=headline,
                    start=event_start,
                )
            for i, sub in enumerate(item["days"]):
                day = (plan["mapping"] or {}).get(i)
                if day is None:
                    plan["notes"].append(f"gallery: sub-event {i + 1} matched no day — skipped")
                    continue
                day_count = sum(1 for img in images if img.event_day_id == day.pk)
                if day_count and not force:
                    plan["skips"].append(
                        f"SKIP day {day.event_day_title or day.date.isoformat()!r}: "
                        f"already has {_n_images(day_count)}"
                    )
                    continue
                self._plan_uploads(
                    plan, present, images_root,
                    paths=sub["images"], event_day=day,
                    has_primary=any(img.is_primary for img in images if img.event_day_id == day.pk),
                    alt_prefix=sub["headline"],
                    start=services.next_sort_order(event, day),
                )

        # 5. publish
        if steps["publish"]:
            if event.is_published:
                plan["notes"].append("publish: already published — no change")
            elif not final_public_slug:
                raise CommandError(
                    "Refusing to publish: the event has no public_slug, so it would be published "
                    "but unreachable. Add --set-public-slug."
                )
            else:
                plan["publish"] = True

        return plan

    def _plan_uploads(self, plan, present, images_root, *, paths, event_day, has_primary, alt_prefix, start):
        """
        ``present`` is the file names of images that existed BEFORE this run,
        across the whole event, and is not added to here. The manifest itself
        repeats photographs across targets (the golden cover is also in the
        photoshoot day's gallery) and the static site showed them in both
        places, so only re-uploading something already stored is skipped.
        Repeats within one target were already collapsed by build_plan.
        """
        n = 0
        for position, web_path in enumerate(paths):
            if _file_key(web_path) in present:
                plan["notes"].append(
                    f"gallery: {Path(web_path).name} already on the event — skip"
                )
                continue
            # A day with no cover gets the manifest's day image (always first
            # in sub["images"]) as its primary — the card the overview shows.
            is_primary = event_day is not None and not has_primary and position == 0
            plan["uploads"].append({
                "local": self._local(images_root, web_path), "web_path": web_path,
                "event_day": event_day, "is_primary": is_primary,
                "sort_order": start + n, "alt": f"{alt_prefix} — photo {position + 1}",
            })
            n += 1

    def _local(self, images_root: Path, web_path: str) -> Path:
        local = images_root / web_path.lstrip("/")
        if not local.is_file():
            raise CommandError(f"Image not found: {local}")
        return local

    # ── output ───────────────────────────────────────────────────────────────

    def _print(self, plan):
        event, item = plan["event"], plan["item"]
        w = self.stdout.write
        w("")
        w(f"EVENT id={event.pk} internal slug={event.slug!r}")
        w(
            f"  now: public_slug={event.public_slug!r} is_published={event.is_published} "
            f"days={len(plan['days'])} images={len(plan['images'])} "
            f"(event-level {sum(1 for i in plan['images'] if i.event_day_id is None)})"
        )

        if plan["mapping"] is not None and item["days"]:
            w(f"DAY MAPPING ({plan['mapping_how']}):")
            for i, sub in enumerate(item["days"]):
                day = plan["mapping"].get(i)
                w(
                    f"  sub-event {i + 1} [{sub['event_day_title']}] slug={sub['static_slug']!r}  ->  "
                    + (
                        f"day {day.pk} date={day.date} [{day.event_day_title or ''}] "
                        f"headline={day.headline!r} slug={day.slug!r}"
                        if day else "NO DAY"
                    )
                )

        if plan["force_gallery"]:
            w("")
            w("!" * 78)
            w("WARNING: --force-gallery — uploading into galleries that ALREADY HAVE IMAGES.")
            w("Only file names are compared. Photos uploaded through the portal are stored")
            w("under other names (UUIDs on prod), so every one of them that is also in the")
            w("manifest WILL BE DUPLICATED. Check the UPLOAD lines below before --commit.")
            w("!" * 78)
            w("")
        w("PLAN:")
        actions = 0
        if plan["public_slug"]:
            old, new = plan["public_slug"]
            w(f"  SET public_slug {old!r} -> {new!r}")
            actions += 1
        for day, old, new in plan["day_slugs"]:
            w(f"  SET day {day.pk} slug {old!r} -> {new!r}")
            actions += 1
        if plan["cover"]:
            if plan["cover"][0] == "promote":
                w(f"  PROMOTE existing event-level image {plan['cover'][1].pk} to cover")
            else:
                w(f"  UPLOAD cover {plan['cover'][2]} -> event level, is_primary=True, sort_order={plan['cover'][3]}")
            actions += 1
        for up in plan["uploads"]:
            where = f"day {up['event_day'].pk}" if up["event_day"] else "event level"
            w(
                f"  UPLOAD {up['web_path']} -> {where}, sort_order={up['sort_order']}"
                + (", is_primary=True" if up["is_primary"] else "")
            )
            actions += 1
        if plan["publish"]:
            w("  SET is_published False -> True")
            actions += 1
        for skip in plan["skips"]:
            w(f"  {skip}")
        if not actions:
            w("  (nothing to do)")
        for note in plan["notes"]:
            w(f"  - {note}")
        uploads = len(plan["uploads"]) + (1 if plan["cover"] and plan["cover"][0] == "upload" else 0)
        w(f"Summary: {actions} change(s), {uploads} upload(s).")

    # ── apply (commit only) ──────────────────────────────────────────────────

    def _apply(self, plan):
        event = plan["event"]

        if plan["public_slug"]:
            event.public_slug = plan["public_slug"][1]
            event.save(update_fields=["public_slug", "updated_at"])

        if plan["day_slugs"]:
            # Clear first, so two days swapping slugs never trip the per-event
            # unique constraint halfway through. update() skips save(), which
            # would otherwise regenerate a slug for the now-blank rows.
            EventDay.objects.filter(pk__in=[d.pk for d, _, _ in plan["day_slugs"]]).update(slug=None)
            for day, _old, new in plan["day_slugs"]:
                day.slug = new
                day.save(update_fields=["slug", "updated_at"])

        if plan["cover"]:
            if plan["cover"][0] == "promote":
                services.set_primary_image(plan["cover"][1])
            else:
                self._upload(
                    plan["cover"][1], event=event, event_day=None, is_primary=True,
                    sort_order=plan["cover"][3],
                    alt=f"{plan['item']['fields']['headline']} — cover",
                )

        for up in plan["uploads"]:
            self._upload(
                up["local"], event=event, event_day=up["event_day"], is_primary=up["is_primary"],
                sort_order=up["sort_order"], alt=up["alt"],
            )

        # Last, so a failed upload above rolls back before anything goes public.
        if plan["publish"]:
            event.is_published = True
            event.save(update_fields=["is_published", "updated_at"])

    def _upload(self, local: Path, *, event, event_day, is_primary, sort_order, alt):
        img = EventImage(
            event=event, event_day=event_day, is_primary=is_primary,
            sort_order=sort_order, alt_text=alt[:255],
        )
        with open(local, "rb") as fh:
            img.image.save(local.name, File(fh), save=False)
        img.save()
        self.stdout.write(f"  uploaded {img.image.name}")
