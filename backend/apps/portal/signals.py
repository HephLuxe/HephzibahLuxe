import logging

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from . import services
from .models import ClientPortal

logger = logging.getLogger(__name__)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_client_portal(sender, instance, created, **kwargs):
    if instance.role == "client":
        # A post_save receiver has no request, so there is no request.user to
        # stamp — which is why every auto-created portal used to come back with
        # created_by_display: "". The actor is inherited from the user row
        # instead: whoever registered the client also created their portal, and
        # UserManager.create_user sets User.created_by before this fires.
        ClientPortal.objects.get_or_create(
            user=instance,
            defaults={"created_by": instance.created_by},
        )


@receiver(post_save, sender=ClientPortal)
def seed_default_team_members(sender, instance, created, **kwargs):
    """Auto-assign the default 'Meet Your Team' contacts to a new portal."""
    if not created:
        return
    try:
        services.seed_default_team_members(instance)
    except Exception:
        # Convenience seeding — never let it break portal creation.
        logger.exception("Failed to seed default team members for portal %s", instance.pk)