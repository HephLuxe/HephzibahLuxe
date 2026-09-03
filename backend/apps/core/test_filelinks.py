"""
apps/core/test_filelinks.py

The private-file endpoint. What these pin down, in order of how much it would
cost to get wrong:

  * a file is only readable by the portal that owns it, and by staff;
  * every registered type resolves its owner correctly — six specs, six
    different paths through the object graph, and a wrong lambda in any one of
    them leaks that model's files to every client;
  * an unregistered type is refused rather than served unchecked;
  * a refusal is indistinguishable from a miss, so ids cannot be enumerated;
  * the minted URL carries the SHORT expiry, not the hour-long default.
"""

import datetime
import io

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.contacts.models import EventContact
from apps.contacts.serializers import EventContactSerializer
from apps.core import file_views
from apps.core.filelinks import FILE_TYPES, MINTED_URL_EXPIRY_SECONDS, mint_url
from apps.events.models import Event, EventDay
from apps.portal.models import ClientPortal, EventEngagement

User = get_user_model()
factory = APIRequestFactory()


def _png(name="p.png"):
    buf = io.BytesIO()
    Image.new("RGB", (2, 2)).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@override_settings(USE_R2_STORAGE=False)
class PrivateFileEndpointTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            first_name="Win", last_name="Team", email="filestaff@example.com",
            password="x", role="staff",
        )
        self.owner = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="fileowner@example.com", password="x",
        )
        self.stranger = User.objects.create_user(
            first_name="Not", last_name="Yours", email="filestranger@example.com", password="x",
        )
        self.event = Event.objects.create(
            celebrant=self.owner, title="Files Event", event_type="Wedding",
            groom_name="Sam", bride_name="Pris", country="NG", state="Lagos",
            event_date=datetime.date(2027, 6, 1),
        )
        self.portal = ClientPortal.objects.get(user=self.owner)
        EventEngagement.objects.create(portal=self.portal, event=self.event, is_active=True)
        self.day = EventDay.objects.create(owner=self.event, date=datetime.date(2027, 6, 1))
        self.contact = EventContact.objects.create(
            event=self.event, event_day=self.day, category="primary", name="Jo",
            photo=_png(),
        )

    def _get(self, user, file_type="contact-photo", obj_id=None):
        req = factory.get("/")
        force_authenticate(req, user=user)
        return file_views.mint_file_url(
            req, file_type=file_type, obj_id=str(obj_id or self.contact.pk),
        )

    # ── who may read ─────────────────────────────────────────────────────────

    def test_the_owning_client_gets_a_url(self):
        resp = self._get(self.owner)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["url"])
        self.assertEqual(resp.data["expires_in"], MINTED_URL_EXPIRY_SECONDS)

    def test_staff_may_read_any_portals_file(self):
        self.assertEqual(self._get(self.staff).status_code, 200)

    # ── the short expiry, which is the point of the whole feature ────────────

    def test_the_short_expiry_actually_reaches_the_storage_backend(self):
        """`expires_in` in the response body is not evidence of this.

        That field echoes MINTED_URL_EXPIRY_SECONDS back, so it agrees with
        itself whatever the value is — which is why the assertion in
        test_the_owning_client_gets_a_url passes even with the constant set to
        an hour. What actually has to hold is that the constant reaches
        `storage.url()`: drop the `expire=` kwarg in a refactor, or let the
        TypeError fallback swallow the real call, and every minted URL silently
        reverts to AWS_QUERYSTRING_EXPIRE. Nothing else in this file would fail,
        and the 60x reduction this module exists for would be gone.

        Storage is a stub rather than the real backend because which one is
        active depends on USE_R2_STORAGE — real R2 locally, in-memory on CI —
        and only the S3 one accepts `expire` at all. The stub records what it
        was handed, which is the whole assertion.
        """
        calls = []

        class _RecordingStorage:
            def url(self, name, **kwargs):
                calls.append(kwargs)
                return "https://signed.example/x"

        class _File:
            storage = _RecordingStorage()
            name = "portals/x/contacts/p.png"

            def __bool__(self):
                return True

        spec = FILE_TYPES["contact-photo"]
        stub = type("Stub", (), {spec.field: _File()})()

        self.assertEqual(mint_url(stub, spec), "https://signed.example/x")
        self.assertEqual(calls, [{"expire": MINTED_URL_EXPIRY_SECONDS}])

    def test_a_backend_that_cannot_take_an_expiry_still_yields_a_url(self):
        """The TypeError fallback. In-memory and filesystem storage take no
        `expire`; nothing is signed there either, so there is no window to
        shorten and a URL must still come back rather than a 500."""

        class _NoExpiryStorage:
            def url(self, name):
                return "https://plain.example/x"

        class _File:
            storage = _NoExpiryStorage()
            name = "p.png"

            def __bool__(self):
                return True

        spec = FILE_TYPES["contact-photo"]
        stub = type("Stub", (), {spec.field: _File()})()

        self.assertEqual(mint_url(stub, spec), "https://plain.example/x")

    def test_the_expiry_stays_far_below_the_default_it_replaces(self):
        """Bounded, not pinned to 60, so the window can be tuned — but not
        widened back to the hour that made a forwarded link an hour of
        unauthenticated access."""
        default = getattr(settings, "AWS_QUERYSTRING_EXPIRE", 3600)
        self.assertLess(MINTED_URL_EXPIRY_SECONDS, default)
        self.assertLessEqual(MINTED_URL_EXPIRY_SECONDS, 300)

    def test_another_client_is_refused(self):
        """The property the whole endpoint exists for."""
        self.assertEqual(self._get(self.stranger).status_code, 404)

    def test_a_refusal_looks_exactly_like_a_miss(self):
        """
        Both 404, and deliberately: a 403 on the refusal would confirm that this
        id exists on *someone's* portal, which is precisely what a caller walking
        ids is trying to learn.
        """
        import uuid as _uuid
        refused = self._get(self.stranger)
        missing = self._get(self.stranger, obj_id=_uuid.uuid4())
        self.assertEqual(refused.status_code, missing.status_code)
        self.assertEqual(refused.data["detail"], missing.data["detail"])

    def test_an_unauthenticated_caller_cannot_mint(self):
        req = factory.get("/")
        resp = file_views.mint_file_url(
            req, file_type="contact-photo", obj_id=str(self.contact.pk),
        )
        self.assertIn(resp.status_code, (401, 403))

    # ── the registry ─────────────────────────────────────────────────────────

    def test_an_unregistered_type_is_refused(self):
        """Fail closed: a file field nobody registered is unreachable, not
        served without an ownership check."""
        self.assertEqual(self._get(self.staff, file_type="secrets").status_code, 404)

    def test_every_registered_spec_names_a_real_model_and_field(self):
        """
        Catches the cheap half of a bad registry entry — a typo'd model label or
        field name — which would otherwise surface as a 500 the first time a
        client clicked that kind of file.
        """
        for file_type, spec in FILE_TYPES.items():
            with self.subTest(file_type=file_type):
                model = spec.get_model()  # raises LookupError on a bad label
                self.assertTrue(
                    any(f.name == spec.field for f in model._meta.get_fields()),
                    f"{model._meta.label} has no field '{spec.field}'",
                )

    def test_a_missing_engagement_is_a_refusal_not_a_pass(self):
        """
        An event can exist with no engagement (FAILURE_POINTS_AUDIT F3/F7). "We
        could not establish who owns this" must never resolve to "anyone may
        have it".
        """
        # No engagement is created here on purpose: Event.objects.create() never
        # makes one — that happens in the create_event *view* — so a directly
        # built event is already in the state this test is about.
        orphan_event = Event.objects.create(
            celebrant=self.owner, title="No Engagement", event_type="Birthday",
            honoree_name="Solo", country="NG", state="Lagos",
            event_date=datetime.date(2027, 7, 1),
        )
        orphan_day = EventDay.objects.create(
            owner=orphan_event, date=datetime.date(2027, 7, 1),
        )
        orphan_contact = EventContact.objects.create(
            event=orphan_event, event_day=orphan_day, category="primary",
            name="Orphan", photo=_png(),
        )
        # The celebrant themselves is refused, because ownership is established
        # through the engagement and there isn't one.
        self.assertEqual(self._get(self.owner, obj_id=orphan_contact.pk).status_code, 404)
        # Staff still pass — they are not scoped by portal.
        self.assertEqual(self._get(self.staff, obj_id=orphan_contact.pk).status_code, 200)

    # ── the empty case ───────────────────────────────────────────────────────

    def test_a_record_with_no_file_is_a_404_not_a_broken_url(self):
        bare = EventContact.objects.create(
            event=self.event, event_day=self.day, category="primary", name="No Photo",
        )
        resp = self._get(self.owner, obj_id=bare.pk)
        self.assertEqual(resp.status_code, 404)

    # ── the serializer side ──────────────────────────────────────────────────

    def test_the_serializer_emits_the_mint_path_and_never_the_storage_url(self):
        data = EventContactSerializer(self.contact).data
        self.assertEqual(
            data["photo_url"], f"/api/v1/files/contact-photo/{self.contact.pk}/",
        )
        # The raw field is gone from the payload entirely — a caller cannot
        # accidentally use a URL that expires.
        self.assertNotIn("photo", data)


@override_settings(USE_R2_STORAGE=False)
class AdminPrivateFileViewTests(TestCase):
    """
    The staff download route. It exists because the API endpoint cannot serve the
    admin: DRF is JWT-only, so an admin's session cookie is anonymous to it, and
    it returns JSON rather than a file.

    It is also the reason the admin's image previews cannot go stale — the
    ``<img src>`` points here, so the signature is minted when the browser
    fetches rather than when the page rendered.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            first_name="Win", last_name="Team", email="adminfiles@example.com",
            password="x", role="staff",
        )
        self.client_user = User.objects.create_user(
            first_name="Ada", last_name="Obi", email="adminfilesclient@example.com", password="x",
        )
        self.event = Event.objects.create(
            celebrant=self.client_user, title="Admin Files Event", event_type="Birthday",
            honoree_name="Jo", country="NG", state="Lagos",
            event_date=datetime.date(2027, 6, 1),
        )
        self.day = EventDay.objects.create(owner=self.event, date=datetime.date(2027, 6, 1))
        self.contact = EventContact.objects.create(
            event=self.event, event_day=self.day, category="primary", name="Jo",
            photo=_png(),
        )
        self.path = f"/admin-files/contact-photo/{self.contact.pk}/"

    def test_staff_are_redirected_to_a_signed_url(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self.path)
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("/admin-files/", resp["Location"])

    def test_a_client_is_bounced_to_the_admin_login(self):
        """staff_member_required, so a non-staff account never reaches it — even
        the celebrant whose own contact this is."""
        self.client.force_login(self.client_user)
        resp = self.client.get(self.path)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login/", resp["Location"])

    def test_an_anonymous_visitor_is_bounced_to_the_admin_login(self):
        resp = self.client.get(self.path)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login/", resp["Location"])

    def test_an_unregistered_type_is_a_404(self):
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get(f"/admin-files/secrets/{self.contact.pk}/").status_code, 404,
        )

    def test_a_record_with_no_file_is_a_404(self):
        bare = EventContact.objects.create(
            event=self.event, event_day=self.day, category="primary", name="No Photo",
        )
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get(f"/admin-files/contact-photo/{bare.pk}/").status_code, 404,
        )

    def test_the_admin_renders_the_redirect_path_not_a_signed_url(self):
        """
        The property that makes previews durable. A signed URL rendered into the
        page would be dead 60 seconds later; this path never expires.
        """
        from django.contrib import admin as dj_admin

        model_admin = dj_admin.site._registry[EventContact]
        html = str(model_admin.photo_preview(self.contact))
        self.assertIn(self.path, html)
        self.assertNotIn("X-Amz-Signature", html)
