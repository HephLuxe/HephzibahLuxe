"""
apps/events/test_apply_portfolio_alignment.py

apply_portfolio_alignment against a local DB built in the exact prod "before"
shape the checked-in plan describes: the same event, day and image ids, the
same days per event, the same sort orders, primaries and publication.

Two things cannot be reproduced from the repo and are substituted in a derived
copy of the plan (everything else is the real plan, unmodified):

  * the hashes of prod's internal slugs and storage keys — the fixture's own
    slugs and keys are hashed instead;
  * the sha256 of each upload — the fixture writes tiny stand-in JPEGs under
    the real file names and records their hashes.
"""

import copy
import datetime
import hashlib
import io
import json
from pathlib import Path
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from PIL import Image
from rest_framework.test import APIClient

from apps.events.management.commands import apply_portfolio_alignment as align
from apps.events.management.commands.import_static_portfolio import DEFAULT_MANIFEST, build_plan
from apps.events.models import Event, EventDay, EventImage
from apps.events.public_views import _LIST_CACHE_KEY, _detail_cache_key
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db

REAL_PLAN = json.loads(align.DEFAULT_PLAN.read_text())


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jpeg(seed: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), ((seed * 37) % 256, (seed * 91) % 256, (seed * 13) % 256)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def storage():
    fresh = InMemoryStorage(base_url="/media/")
    with mock.patch.object(EventImage._meta.get_field("image"), "storage", fresh):
        yield fresh


@pytest.fixture
def prod_before(storage, tmp_path):
    """The prod before state, a derived plan file, and an images root."""
    plan = copy.deepcopy(REAL_PLAN)
    user = get_user_model().objects.create_user(
        first_name="Test", last_name="Client", email="align@example.com", password="x",
    )
    portal_id = user.portal.id

    for pe in plan["events"]:
        identity = pe["identity"]
        names = {"honoree_name": "Test"} if identity["event_type"] == "Birthday" else {"event_name": "Forum"}
        event = Event.objects.create(
            id=pe["id"], celebrant=user, title=f"Test Client Event {pe['id']}",
            event_type=identity["event_type"], headline=identity["headline"],
            is_published=identity["is_published"], public_slug=pe["before"]["public_slug"],
            country="Nigeria", state="Lagos", event_date=datetime.date(2021, 1, 1), **names,
        )
        pe["internal_slug_sha256"] = _sha(event.slug.encode())

    for pd in plan["days"]:
        EventDay.objects.create(
            id=pd["id"], owner_id=pd["identity"]["owner_id"], headline=pd["identity"]["headline"],
            date=datetime.date.fromisoformat(pd["identity"]["date"]), **pd["before"],
        )

    for n, pi in enumerate(plan["images"]):
        event = Event.objects.get(pk=pi["event_id"])
        folder = f"portals/{portal_id}/events/{event.pk}"
        legacy = f"portals/{portal_id}/events/{event.pk}-{event.slug}"
        rest = (f"days/{pi['event_day_id']}/" if pi["event_day_id"] else "") + f"gallery/{pi['id']}/{pi['filename']}"
        # Event 2 is stored as if rekey_public_image_paths already ran, the
        # rest on legacy keys: the plan must hold either way.
        key = f"{folder}/{rest}" if pi["event_id"] == 2 else f"{legacy}/{rest}"
        storage.save(key, ContentFile(_jpeg(n)))
        EventImage.objects.create(
            id=pi["id"], event_id=pi["event_id"], event_day_id=pi["event_day_id"],
            image=key, **pi["before"],
        )
        pi["key_sha256"] = [_sha(f"{legacy}/{rest}".encode()), _sha(f"{folder}/{rest}".encode())]

    root = tmp_path / "public"
    for n, up in enumerate(plan["uploads"]):
        path = root / "images" / "portfoliopage" / up["filename"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(_jpeg(1000 + n))
        up["sha256"] = _sha(path.read_bytes())

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    return {"plan": plan, "plan_path": plan_path, "root": root}


def _run(ctx, *args, plan_path=None):
    out, err = io.StringIO(), io.StringIO()
    call_command(
        "apply_portfolio_alignment", "--plan", str(plan_path or ctx["plan_path"]),
        "--images-root", str(ctx["root"]), *args, stdout=out, stderr=err,
    )
    return out.getvalue(), err.getvalue()


def _snapshot():
    return (
        list(Event.objects.order_by("pk").values()),
        list(EventDay.objects.order_by("pk").values()),
        list(EventImage.objects.order_by("pk").values()),
    )


def _image_state(pk):
    img = EventImage.objects.get(pk=pk)
    return {k: getattr(img, k) for k in align.IMAGE_FLAGS}


def test_dry_run_passes_and_writes_nothing(prod_before, storage):
    before = _snapshot()
    with mock.patch.object(storage, "save", wraps=storage.save) as save:
        out, err = _run(prod_before)
    assert err == ""
    assert "prod matches the plan's before state exactly" in out
    assert "Dry run: nothing written" in out
    assert out.count("sha256 OK") == len(REAL_PLAN["uploads"])
    assert "'a-golden-50th-an-intimate-two-day-celebration-of-family-faith-joy' -> 'golden-50th'" in out
    assert "(event 1): slug 'event-day-1' -> 'thanksgiving-gathering'\n" in out
    assert "(event 1): slug 'event-day-2' -> 'celebration-night'\n" in out
    assert "UNPUBLISH:\n  legacysixs.jpg (f252dff1-" in out
    assert "title 'Event No." not in out
    assert "2 day slug change(s), 0 day label change(s)" in out
    save.assert_not_called()
    assert _snapshot() == before


@pytest.mark.parametrize("mutation", ["sort", "primary", "extra_image", "missing_image", "extra_day", "key", "slug"])
def test_any_difference_from_the_before_state_aborts(prod_before, storage, mutation):
    plan = prod_before["plan"]
    first = plan["images"][0]["id"]
    if mutation == "sort":
        EventImage.objects.filter(pk=first).update(sort_order=99)
    elif mutation == "primary":
        EventImage.objects.filter(pk=first).update(is_primary=False)
    elif mutation == "extra_image":
        EventImage.objects.create(event_id=3, image="x/extra.jpg")
    elif mutation == "missing_image":
        EventImage.objects.filter(pk=plan["images"][-1]["id"]).delete()
    elif mutation == "extra_day":
        EventDay.objects.create(owner_id=3, date=datetime.date(2026, 1, 1), event_day_title="Extra")
    elif mutation == "key":
        EventImage.objects.filter(pk=first).update(image="somewhere/else.jpg")
    elif mutation == "slug":
        Event.objects.filter(pk=2).update(public_slug="renamed-by-staff")
    before = _snapshot()

    for args in ((), ("--commit",)):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(storage, "save", wraps=storage.save) as save:
            with pytest.raises(CommandError, match="Refusing to proceed"):
                call_command(
                    "apply_portfolio_alignment", "--plan", str(prod_before["plan_path"]),
                    "--images-root", str(prod_before["root"]), *args, stdout=out, stderr=err,
                )
        assert "MISMATCH" in err.getvalue()
        save.assert_not_called()
        assert _snapshot() == before


def test_a_missing_or_altered_local_file_aborts_before_any_upload(prod_before, storage):
    victim = prod_before["root"] / "images" / "portfoliopage" / "lagosones.jpg"
    victim.write_bytes(b"not the planned bytes")
    with mock.patch.object(storage, "save", wraps=storage.save) as save:
        with pytest.raises(CommandError, match="upload file problem"):
            _run(prod_before, "--commit")
    save.assert_not_called()


def test_commit_yields_exactly_the_target_state(prod_before, storage):
    plan = prod_before["plan"]
    cache.set(_LIST_CACHE_KEY, ["stale"])
    for pe in plan["events"]:
        cache.set(_detail_cache_key(pe["before"]["public_slug"]), {"stale": True})
        cache.set(_detail_cache_key(pe["after"]["public_slug"]), {"stale": True})
    notifications = Notification.objects.count()

    out, err = _run(prod_before, "--commit")

    assert err == ""
    assert "Committed. Prod now matches the plan's after state." in out
    for pi in plan["images"]:
        assert _image_state(pi["id"]) == pi["after"], pi["filename"]
    for up in plan["uploads"]:
        img = EventImage.objects.get(pk=up["id"])
        assert _image_state(up["id"]) == {k: up[k] for k in align.IMAGE_FLAGS}
        assert (img.event_id, str(img.event_day_id) if img.event_day_id else None) == (up["event_id"], up["event_day_id"])
        event = img.event
        assert event.slug not in img.image.name
        assert img.image.name.startswith(f"portals/{event.celebrant.portal.id}/events/{event.pk}/")
        assert img.image.name.endswith(f"/gallery/{up['id']}/{up['filename']}")
        with storage.open(img.image.name, "rb") as fh:
            assert _sha(fh.read()) == up["sha256"]
    assert EventImage.objects.count() == len(plan["images"]) + len(plan["uploads"])
    assert {e.pk: e.public_slug for e in Event.objects.all()} == {1: "golden-50th", 2: "intimate-85th", 3: "msme-forum"}
    for pd in plan["days"]:
        day = EventDay.objects.get(pk=pd["id"])
        assert {"slug": day.slug, "event_day_title": day.event_day_title} == pd["after"]
    # Exactly one primary per gallery, as the static site has one cover each.
    for event_id in (1, 2, 3):
        assert EventImage.objects.filter(event_id=event_id, event_day__isnull=True, is_primary=True).count() == 1
    for day in EventDay.objects.all():
        assert day.images.filter(is_primary=True).count() == 1
    assert Notification.objects.count() == notifications
    assert cache.get(_LIST_CACHE_KEY) is None
    for pe in plan["events"]:
        assert cache.get(_detail_cache_key(pe["before"]["public_slug"])) is None
        assert cache.get(_detail_cache_key(pe["after"]["public_slug"])) is None


def test_public_api_order_matches_the_static_manifest(prod_before, storage):
    """After the commit, each event's photographs come out of the public API in
    the static site's order: cover, the rest of the event level, then each day
    by date. (Intimate 85th's photographs sit in its single day on prod, where
    the static site had them at event level; the sequence is the same.)"""
    _run(prod_before, "--commit")
    manifest = {p["slug"]: p for p in build_plan(json.loads(Path(DEFAULT_MANIFEST).read_text()))}
    api = APIClient()
    for slug, item in manifest.items():
        response = api.get(f"/api/v1/portfolio/events/{slug}/")
        assert response.status_code == 200, slug
        body = response.json()
        # The cover is returned once, as cover_image, and left out of images.
        got = [Path(body["cover_image"]["image"]).name]
        got += [Path(i["image"]).name for i in body["images"]]
        for day in body["event_days"]:
            got += [Path(i["image"]).name for i in day["images"]]
        expected = [Path(p).name for p in item["images"]]
        for day in item["days"]:
            expected += [Path(p).name for p in day["images"]]
        assert got == expected, slug
        if item["days"]:
            assert [d["slug"] for d in body["event_days"]] == [d["static_slug"] for d in item["days"]]
            assert [d["event_day_title"] for d in body["event_days"]] == [d["event_day_title"] for d in item["days"]]
    for pe in prod_before["plan"]["events"]:
        assert api.get(f"/api/v1/portfolio/events/{pe['before']['public_slug']}/").status_code == 404


def test_rerun_after_commit_is_a_no_op(prod_before, storage):
    _run(prod_before, "--commit")
    after = _snapshot()
    with mock.patch.object(storage, "save", wraps=storage.save) as save:
        out, err = _run(prod_before)
        assert "Already aligned" in out and err == ""
        out, err = _run(prod_before, "--commit")
        assert "Already aligned" in out and err == ""
    save.assert_not_called()
    assert _snapshot() == after


def test_the_primary_constraint_is_real_and_the_commit_order_respects_it(prod_before, storage):
    """Promoting the new cover BEFORE clearing the old one violates the partial
    unique index — so the commit succeeding (on Postgres, where it is enforced
    per statement) shows the clear-first ordering is what makes it work."""
    plan = prod_before["plan"]
    new_cover = next(pi for pi in plan["images"] if pi["after"]["is_primary"] and not pi["before"]["is_primary"])
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            EventImage.objects.filter(pk=new_cover["id"]).update(is_primary=True)

    out, _ = _run(prod_before, "--commit")
    assert "Committed." in out
    assert EventImage.objects.get(pk=new_cover["id"]).is_primary


def test_a_failed_db_transaction_reports_orphans_and_a_rerun_reuses_them(prod_before, storage):
    plan = prod_before["plan"]
    before = _snapshot()
    real_apply = align.Command._apply

    def apply_then_fail(self, *args, **kwargs):
        real_apply(self, *args, **kwargs)
        raise RuntimeError("connection lost")

    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(align.Command, "_apply", apply_then_fail):
        with pytest.raises(CommandError, match="rolled back: connection lost"):
            call_command(
                "apply_portfolio_alignment", "--plan", str(prod_before["plan_path"]),
                "--images-root", str(prod_before["root"]), "--commit", stdout=out, stderr=err,
            )
    assert _snapshot() == before
    orphans = [line.split("ORPHAN ", 1)[1] for line in err.getvalue().splitlines() if "ORPHAN " in line]
    assert len(orphans) == len(plan["uploads"])
    assert all(storage.exists(key) for key in orphans)

    with mock.patch.object(storage, "save", wraps=storage.save) as save:
        out, _ = _run(prod_before, "--commit")
    save.assert_not_called()
    assert out.count("  present ") == len(plan["uploads"])
    assert "Committed." in out
