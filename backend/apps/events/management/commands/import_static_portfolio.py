"""
apps/events/management/commands/import_static_portfolio.py

Imports the portfolio events that used to be hard-coded in the frontend
(frontend/data/portfolio.ts) as published Event / EventDay / EventImage rows, so
the public portfolio API (apps/events/public_views.py) can serve them.

    python manage.py import_static_portfolio                  # dry run: prints the plan
    python manage.py import_static_portfolio --commit         # writes rows + uploads images
    python manage.py import_static_portfolio --commit --portal <portal-uuid-or-user-email>

The manifest
------------
apps/events/fixtures/static_portfolio.json is generated, not hand-copied, by
evaluating the TypeScript module with node (see RUNBOOK.md "Local development")
so it is exactly what the frontend shipped.

Dry run (the default)
---------------------
Nothing is written. The run reads the database only to resolve --portal and to
see which public slugs already exist, inside a transaction that is marked READ ONLY on
Postgres and rolled back at the end, and it never opens the media storage —
image files are only checked for existence on local disk.

Mapping
-------
* The static slug (golden-50th, ...) -> ``Event.public_slug``, the public URL.
  The internal ``Event.slug`` is left to ``Event.save()`` (derived from
  ``title``); it keys the portal and never appears publicly.
* ``title`` and ``headline`` are both the static title (``title`` is never public).
* ``event_date`` is 1 January of the static ``year`` — the public API only
  publishes the year. Days of a multi-day event are dated event_date + n days,
  in manifest order, so they sort in the order the frontend showed them.
* Sub-event ``subtitle`` ("Event No. 1") -> ``EventDay.event_day_title`` (the
  eyebrow), ``title`` -> ``EventDay.headline``, per the field help texts on
  EventDay, and ``slug`` -> ``EventDay.slug`` (the day's public URL segment).
* Cover -> event-level EventImage, is_primary. A single-day event's gallery ->
  further event-level EventImages (exposed as ``images`` on the public detail).
  A sub-event's ``image`` -> primary EventImage on its day, then its gallery.
* Testimonial rows and gallery ``ratios`` have no backend field and are skipped (counted in the output). Repeated image paths within one
  target (an event's own gallery, or one day's) are uploaded once.

Ownership: an Event has no portal FK. It reaches a portal through
``celebrant`` (User) -> ``User.portal`` (ClientPortal), which is only used to
build storage paths. ``--portal`` is optional; without it the events have no
celebrant (portfolio-only, not listed in any client's portal) and their files
are stored under ``portals/unknown/...``.

Side effects: creating these rows fires only the events app's post_save cache
invalidation (signals.py). No EventEngagement is created, so no document-hub
seeding runs, and no client notification is queued — those are triggered from
the API views, which this command does not go through.

Idempotent: an event whose PUBLIC slug already exists (published or not, any
owner) is skipped. To map the static content onto an event that already exists
(a real client's event), use ``attach_static_portfolio`` instead.
"""

import json
import uuid
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.events.models import Event, EventDay, EventImage
from apps.portal.models import ClientPortal

DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "fixtures" / "static_portfolio.json"

CATEGORY_TO_EVENT_TYPE = {
    "Weddings": "Wedding",
    "Birthdays": "Birthday",
    "Corporate": "Corporate",
    "Social Events": "Social Events",
}


def _paragraphs(value) -> str:
    return "\n\n".join(value or [])


def _flatten_gallery(rows) -> tuple[list[str], int]:
    """Image paths in display order, plus the number of testimonial rows dropped."""
    images, testimonials = [], 0
    for row in rows or []:
        if row.get("type") == "images":
            images.extend(row.get("images") or [])
        elif row.get("type") == "testimonial":
            testimonials += 1
    return images, testimonials


def _dedupe(paths: list[str]) -> tuple[list[str], int]:
    seen, out = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out, len(paths) - len(out)


def _split_location(location: str, slug: str) -> tuple[str, str]:
    """'Lagos, Nigeria' -> (state='Lagos', country='Nigeria')."""
    state, sep, country = location.rpartition(",")
    if not sep or not state.strip() or not country.strip():
        raise CommandError(f"{slug}: cannot split location {location!r} into state, country.")
    return state.strip(), country.strip()


def build_plan(manifest: list[dict]) -> list[dict]:
    """Pure transform of the manifest into the rows to create. No DB access."""
    plan = []
    for item in manifest:
        slug = item["slug"]
        try:
            event_type = CATEGORY_TO_EVENT_TYPE[item["category"]]
        except KeyError:
            raise CommandError(f"{slug}: unknown category {item.get('category')!r}.")
        state, country = _split_location(item["location"], slug)
        event_date = date(int(item["year"]), 1, 1)
        headline = item["title"]

        gallery, testimonials = _flatten_gallery(item.get("gallery"))
        event_images, dupes = _dedupe([item["coverImage"], *gallery])

        days = []
        for n, sub in enumerate(item.get("subEvents") or []):
            sub_gallery, sub_testimonials = _flatten_gallery(sub.get("gallery"))
            day_images, day_dupes = _dedupe([sub["image"], *sub_gallery])
            testimonials += sub_testimonials
            dupes += day_dupes
            days.append({
                "event_day_title": sub["subtitle"],
                "headline": sub["title"],
                "content": _paragraphs(sub.get("description")),
                "date": event_date + timedelta(days=n),
                "images": day_images,  # first one is the day's primary
                "static_slug": sub.get("slug"),
            })

        plan.append({
            "slug": slug,
            "fields": {
                "title": headline,
                "headline": headline,
                "description": _paragraphs(item.get("description")),
                "event_type": event_type,
                "state": state,
                "country": country,
                "event_date": event_date,
                "is_published": True,
            },
            "images": event_images,  # first one is the cover
            "days": days,
            "skipped_testimonials": testimonials,
            "skipped_duplicate_images": dupes,
            "skipped_ratios": sum(
                1 for row in [*(item.get("gallery") or []),
                              *(r for s in item.get("subEvents") or [] for r in s.get("gallery") or [])]
                if row.get("ratios")
            ),
        })
    return plan


class Command(BaseCommand):
    help = (
        "Import the static frontend portfolio (fixtures/static_portfolio.json) as "
        "published events. Dry run unless --commit."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--manifest", default=str(DEFAULT_MANIFEST),
            help="JSON manifest generated from frontend/data/portfolio.ts.",
        )
        parser.add_argument(
            "--images-root", default=str(Path(settings.BASE_DIR).parent / "frontend" / "public"),
            help="Directory the manifest's /images/... paths are relative to.",
        )
        parser.add_argument(
            "--portal", default=None,
            help=(
                "Optional owner: a ClientPortal UUID, or the email of a user who has "
                "a portal. Its user becomes the events' celebrant. Omit for no celebrant."
            ),
        )
        parser.add_argument(
            "--commit", action="store_true",
            help="Actually create rows and upload images. Without it nothing is written.",
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _load_manifest(self, path: str) -> list[dict]:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Cannot read manifest {path}: {exc}")
        if not isinstance(data, list):
            raise CommandError("Manifest must be a JSON list of events.")
        return data

    def _resolve_celebrant(self, identifier):
        if not identifier:
            return None
        portal = None
        try:
            portal = ClientPortal.objects.select_related("user").filter(
                pk=uuid.UUID(identifier),
            ).first()
        except ValueError:
            user = get_user_model().objects.filter(email__iexact=identifier).first()
            portal = getattr(user, "portal", None) if user else None
        if portal is None:
            raise CommandError(f"No ClientPortal found for --portal {identifier!r}.")
        return portal.user

    def _local_file(self, images_root: Path, web_path: str) -> Path:
        return images_root / web_path.lstrip("/")

    # ── main ─────────────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        commit = options["commit"]
        images_root = Path(options["images_root"]).resolve()
        plan = build_plan(self._load_manifest(options["manifest"]))

        missing = sorted({
            p
            for ev in plan
            for p in [*ev["images"], *(i for d in ev["days"] for i in d["images"])]
            if not self._local_file(images_root, p).is_file()
        })
        if missing:
            raise CommandError(
                f"{len(missing)} image(s) not found under {images_root}: " + ", ".join(missing)
            )

        mode = "COMMIT" if commit else "DRY RUN (nothing will be written)"
        self.stdout.write(f"import_static_portfolio — {mode}")
        self.stdout.write(f"  manifest:    {options['manifest']}")
        self.stdout.write(f"  images root: {images_root}")

        if commit:
            self._run(plan, images_root, options["portal"], commit=True)
        else:
            # Reads only. On Postgres the transaction is also marked READ ONLY,
            # so any write that slipped in would fail instead of landing; the
            # rollback is a second guarantee on every backend.
            with transaction.atomic():
                if connection.vendor == "postgresql":
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION READ ONLY")
                self._run(plan, images_root, options["portal"], commit=False)
                transaction.set_rollback(True)

    def _run(self, plan, images_root, portal_identifier, *, commit):
        celebrant = self._resolve_celebrant(portal_identifier)
        if celebrant is None:
            self.stdout.write("  celebrant:   none (portfolio-only; files under portals/unknown/)")
        else:
            self.stdout.write(
                f"  celebrant:   user id {celebrant.pk} (role={celebrant.role}), "
                f"portal {celebrant.portal.pk}"
            )

        created = skipped = 0
        for ev in plan:
            self.stdout.write("")
            if Event.objects.filter(public_slug=ev["slug"]).exists():
                skipped += 1
                self.stdout.write(f"SKIP {ev['slug']}: an event with this public slug already exists.")
                continue

            self._describe(ev)
            if commit:
                with transaction.atomic():
                    self._create(ev, images_root, celebrant)
                self.stdout.write(self.style.SUCCESS(f"  created {ev['slug']}"))
            created += 1

        verb = "created" if commit else "would create"
        self.stdout.write("")
        self.stdout.write(f"Done: {verb} {created}, skipped {skipped} (already exist).")

    def _describe(self, ev):
        f = ev["fields"]
        self.stdout.write(f"CREATE {ev['slug']} (public_slug)")
        self.stdout.write(f"  headline:    {f['headline']}")
        self.stdout.write(
            f"  type={f['event_type']} state={f['state']} country={f['country']} "
            f"event_date={f['event_date'].isoformat()} is_published=True"
        )
        self.stdout.write(f"  description: {len(f['description'])} chars")
        self.stdout.write(
            f"  event-level images: {len(ev['images'])} "
            f"(cover {ev['images'][0]} + {len(ev['images']) - 1} gallery)"
        )
        for d in ev["days"]:
            self.stdout.write(
                f"  day {d['date'].isoformat()} slug={d['static_slug']} [{d['event_day_title']}] "
                f"{d['headline']} — {len(d['images'])} images (primary {d['images'][0]})"
            )
        self.stdout.write(
            f"  skipped: {ev['skipped_testimonials']} testimonial row(s), "
            f"{ev['skipped_ratios']} gallery row ratio hint(s), "
            f"{ev['skipped_duplicate_images']} duplicate image path(s)"
        )

    def _create(self, ev, images_root, celebrant):
        event = Event.objects.create(celebrant=celebrant, public_slug=ev["slug"], **ev["fields"])

        headline = ev["fields"]["headline"]
        for n, web_path in enumerate(ev["images"]):
            self._add_image(
                images_root, web_path, event=event, event_day=None,
                is_primary=(n == 0), sort_order=n, alt=f"{headline} — photo {n + 1}",
            )

        for d in ev["days"]:
            day = EventDay.objects.create(
                owner=event, event_day_title=d["event_day_title"], headline=d["headline"],
                content=d["content"], date=d["date"], slug=d["static_slug"] or None,
            )
            for n, web_path in enumerate(d["images"]):
                self._add_image(
                    images_root, web_path, event=event, event_day=day,
                    is_primary=(n == 0), sort_order=n, alt=f"{d['headline']} — photo {n + 1}",
                )

    def _add_image(self, images_root, web_path, *, event, event_day, is_primary, sort_order, alt):
        img = EventImage(
            event=event, event_day=event_day, is_primary=is_primary,
            sort_order=sort_order, alt_text=alt[:255],
        )
        local = self._local_file(images_root, web_path)
        with open(local, "rb") as fh:
            # save=False then save(): the upload path needs the row's own uuid,
            # which exists from instantiation, and the event/day already saved.
            img.image.save(local.name, File(fh), save=False)
        img.save()
