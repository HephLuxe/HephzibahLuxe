"""
apps/document_hub/signals.py

Two responsibilities, all wired up in DocumentHubConfig.ready():

1. Auto-seed portal boilerplate from PortalDefaults (see services.py):
     * a new EventEngagement -> its Document Hub is populated with copies of the
       configured Service Agreement / Welcome Booklet / FAQ templates, and the
       engagement is assigned its reference segment (PSW###) first.
     * a new ClientPortal    -> its welcome_message is set from the default.

2. Fill auto-generated reference codes (HL-PSW001-INV001, …) via pre_save on the
   three coded models. Doing it in pre_save — not the views — means EVERY
   creation path (API, Django admin, the seed above, the shell) gets a code.

These signals live here (not in portal) because document_hub already depends on
portal — so listening to portal's models adds no new dependency direction and
no import cycle.

Listening on EventEngagement's post_save with `created=True` covers every
creation path at once — events.create_event (both branches) and
portal.activate_engagement's get_or_create — and never fires on re-activation
(that's an UPDATE), so switching back to a past event doesn't re-seed it.
"""

import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.portal.models import ClientPortal, EventEngagement

from . import services
from .models import ClientDocument, Invoice, Receipt

logger = logging.getLogger(__name__)


@receiver(post_save, sender=EventEngagement)
def seed_engagement_documents(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        # Assign the reference segment BEFORE seeding, so the seeded Service
        # Agreement's pre_save can build its HL-PSW###-C001 code.
        services.assign_engagement_segment(instance)
        services.seed_engagement_documents(instance)
    except Exception:
        # Seeding is a convenience — never let it break engagement creation.
        # Staff can still add the documents manually if a template is misconfigured.
        logger.exception("Failed to seed default documents for engagement %s", instance.pk)


# ── Reference-code generation (fill-if-blank, on any create path) ──

@receiver(pre_save, sender=Invoice)
def fill_invoice_number(sender, instance, **kwargs):
    if not instance.invoice_number and instance.engagement_id:
        instance.invoice_number = services.next_reference_code(instance.engagement, "INV")


@receiver(pre_save, sender=Receipt)
def fill_receipt_number(sender, instance, **kwargs):
    if not instance.receipt_number and instance.engagement_id:
        instance.receipt_number = services.next_reference_code(instance.engagement, "R")


@receiver(pre_save, sender=ClientDocument)
def fill_document_reference_code(sender, instance, **kwargs):
    # Only the signable categories (Service Agreement / Quotation) carry a code.
    type_code = services.TYPE_CODES.get(instance.category)
    if type_code and not instance.reference_code and instance.engagement_id:
        instance.reference_code = services.next_reference_code(instance.engagement, type_code)


@receiver(post_save, sender=ClientPortal)
def apply_default_welcome_message(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        services.apply_default_welcome_message(instance)
    except Exception:
        logger.exception("Failed to apply default welcome message for portal %s", instance.pk)
