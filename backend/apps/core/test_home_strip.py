from io import BytesIO, StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory
from PIL import Image
from rest_framework.test import APIClient

from apps.core.management.commands.import_home_strip import ASSETS
from apps.core.models import HomeStripImage
from apps.core.public_views import home_strip
from apps.core.storages import PublicMediaStorage, select_public_media_storage
from apps.core.throttling import PortfolioRateThrottle
from apps.core.uploads import validate_image_model_field
from apps.events.models import EventImage
from apps.events.public_serializers import PublicEventImageSerializer

pytestmark = pytest.mark.django_db


@pytest.fixture
def storage():
    storage = InMemoryStorage(base_url="/media/")
    with patch.object(HomeStripImage._meta.get_field("image"), "storage", storage):
        yield storage


@pytest.fixture
def source(tmp_path):
    content = BytesIO()
    Image.new("RGB", (2, 2)).save(content, format="JPEG")
    for asset in ASSETS:
        path = tmp_path / asset
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.getvalue())
    return tmp_path


@pytest.mark.parametrize("authorization", [None, "Bearer invalid-token"])
def test_public_list_excludes_unpublished_and_orders(authorization, storage):
    HomeStripImage.objects.create(image="hidden.jpg", sort_order=0)
    HomeStripImage.objects.create(image="last.jpg", sort_order=9, is_published=True)
    HomeStripImage.objects.create(image="first.jpg", alt_text="", sort_order=1, is_published=True)
    client = APIClient()
    if authorization:
        client.credentials(HTTP_AUTHORIZATION=authorization)
    response = client.get("/api/v1/public/home-strip/")
    assert response.status_code == 200
    assert response.json() == [
        {"image": "http://testserver/media/first.jpg", "alt_text": "", "sort_order": 1},
        {"image": "http://testserver/media/last.jpg", "alt_text": "", "sort_order": 9},
    ]
    assert home_strip.cls.throttle_classes == [PortfolioRateThrottle]


def test_empty_list():
    assert APIClient().get("/api/v1/public/home-strip/").json() == []


def test_import_dry_run_has_no_writes(source, storage):
    with patch.object(storage, "save", wraps=storage.save) as save:
        output = StringIO()
        call_command("import_home_strip", source_root=source, stdout=output)
        assert "Dry run; no writes." in output.getvalue()
        assert output.getvalue().count("Would create") == 6
        assert not HomeStripImage.objects.exists()
        save.assert_not_called()


def test_import_idempotent_preserves_admin_edits(source, storage):
    with patch.object(storage, "save", wraps=storage.save) as save:
        call_command("import_home_strip", source_root=source, apply=True, stdout=StringIO())
        assert HomeStripImage.objects.count() == 6
        assert save.call_count == 6
        assert list(HomeStripImage.objects.values_list("import_key", flat=True)) == [
            f"home-strip:{asset}" for asset in ASSETS
        ]
        assert list(HomeStripImage.objects.values_list("sort_order", flat=True)) == list(range(6))
        assert all(row.is_published and row.alt_text == "" for row in HomeStripImage.objects.all())
        row = HomeStripImage.objects.first()
        row.alt_text = "Admin caption"
        row.sort_order = 99
        row.is_published = False
        row.image = "admin-replacement.jpg"
        row.save()
        before = list(HomeStripImage.objects.order_by("id").values())
        call_command("import_home_strip", source_root=source, apply=True, stdout=StringIO())
        assert list(HomeStripImage.objects.order_by("id").values()) == before
        assert save.call_count == 6


@pytest.mark.parametrize("invalid", ["missing", "corrupt"])
def test_import_validates_entire_batch_before_writes(source, storage, invalid):
    last = source / ASSETS[-1]
    if invalid == "missing":
        last.unlink()
    else:
        last.write_bytes(b"not an image")
    with patch.object(storage, "save", wraps=storage.save) as save:
        with pytest.raises(CommandError, match="Invalid image"):
            call_command("import_home_strip", source_root=source, apply=True)
        assert not HomeStripImage.objects.exists()
        save.assert_not_called()


def test_image_field_uses_the_public_media_storage_and_the_ceiling():
    """Same storage callable and path budget as the portfolio galleries, so the
    strip lands in the PUBLIC bucket (unsigned custom-domain URLs on prod)."""
    field = HomeStripImage._meta.get_field("image")
    assert field._storage_callable is select_public_media_storage
    assert EventImage._meta.get_field("image")._storage_callable is select_public_media_storage
    assert field.max_length == 500
    assert validate_image_model_field in field.validators


def test_r2_custom_domain_url_is_served_exactly_like_a_portfolio_image(settings):
    """On prod the public storage returns an absolute https URL on the R2 custom
    domain. Both endpoints must pass it through untouched (no testserver host
    prefixed, no signature) — checked side by side through the real S3 storage
    class, which builds custom-domain URLs without any network call."""
    settings.R2_PUBLIC_URL = "https://media.example.com"
    settings.R2_PUBLIC_BUCKET_NAME = "public-bucket"
    r2 = PublicMediaStorage(access_key="x", secret_key="x", endpoint_url="https://r2.invalid")
    expected = "https://media.example.com/home-strip/one.jpg"
    with patch.object(HomeStripImage._meta.get_field("image"), "storage", r2), \
            patch.object(EventImage._meta.get_field("image"), "storage", r2):
        HomeStripImage.objects.create(image="home-strip/one.jpg", is_published=True)
        strip = APIClient().get("/api/v1/public/home-strip/").json()
        portfolio = PublicEventImageSerializer(
            EventImage(image="home-strip/one.jpg"), context={"request": RequestFactory().get("/")},
        ).data
    assert strip[0]["image"] == expected
    assert portfolio["image"] == expected


@pytest.fixture
def admin_client(client, settings):
    # The suite's settings use the manifest static storage, which has no
    # manifest under test, so any admin page render would 500 on its CSS.
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    admin = get_user_model().objects.create_superuser(
        first_name="Root", last_name="Admin", email="root@example.com", password="Sw0rdfish!23",
    )
    client.force_login(admin)
    return client


def _jpeg(name="strip.jpg"):
    content = BytesIO()
    Image.new("RGB", (4, 4)).save(content, format="JPEG")
    return SimpleUploadedFile(name, content.getvalue(), content_type="image/jpeg")


def _admin_add(admin_client, upload):
    return admin_client.post("/admin/core/homestripimage/add/", {
        "image": upload, "alt_text": "", "sort_order": 0, "is_published": "on",
    })


def test_admin_upload_over_the_ceiling_is_a_form_error_not_a_500(admin_client, storage):
    with patch("apps.core.uploads.MAX_IMAGE_SIZE", 10):
        response = _admin_add(admin_client, _jpeg())
    assert response.status_code == 200
    assert "over the" in response.content.decode()
    assert not HomeStripImage.objects.exists()


def test_admin_upload_of_a_non_image_type_is_refused(admin_client, storage):
    # A GIF is a valid image to Pillow (so ImageField accepts it) but is not
    # one of the ceiling's types: only the content sniff can refuse it.
    content = BytesIO()
    Image.new("RGB", (4, 4)).save(content, format="GIF")
    response = _admin_add(admin_client, SimpleUploadedFile("a.gif", content.getvalue(), content_type="image/gif"))
    assert response.status_code == 200
    assert "must be one of" in response.content.decode()
    assert not HomeStripImage.objects.exists()


def test_admin_upload_within_the_ceiling_is_saved(admin_client, storage):
    response = _admin_add(admin_client, _jpeg())
    assert response.status_code == 302
    row = HomeStripImage.objects.get()
    assert row.image.name.startswith("home-strip/")
    assert storage.exists(row.image.name)
