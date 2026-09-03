"""
apps/events/signals.py

Deletes a gallery image's stored blob when its row goes away — through *any*
path. Wired up in EventsConfig.ready().

Why a signal rather than doing it in the delete view: EventImage rows are
removed from several places, and cascade deletes never pass through view code at
all — `Event.delete()` takes its whole gallery with it, and `EventDay.delete()`
takes that day's. A post_delete receiver is the one hook every path goes
through. Registering it also forces Django off its "fast delete" optimisation for
this model, so the receiver genuinely fires on cascades instead of being skipped
while the rows vanish in a single bulk DELETE.

Note that no such receiver existed for the single-image fields this gallery
replaced (`Event.featured_image`, `EventDay.event_images`) — deleting an event
left its cover in the bucket for ever. That was survivable at one blob per
event; a gallery of dozens per event is not, which is why this ships alongside
the model rather than after it.

Modelled on apps/meetings/signals.py, which does the same job for prep uploads
and documents the transaction reasoning in more detail.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Event, EventDay, EventImage

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=EventImage)
def cleanup_event_image(sender, instance: EventImage, **kwargs) -> None:
    # Storage is NOT transactional, so the blob delete is deferred to on_commit:
    # if the surrounding transaction rolls back, the row comes back and its file
    # must still be there. (This is the same reason Django refuses to auto-delete
    # FileField files.) In autocommit the callback runs straight after the
    # delete commits.
    file = instance.image
    if not file:
        return

    def _delete_blob(name=file.name, storage=file.storage):
        try:
            storage.delete(name)
        except Exception:
            # An already-gone blob must not break a delete that has otherwise
            # succeeded — the row is the record of truth, and this runs after
            # commit where raising would surface as a 500 on a completed action.
            logger.warning("Failed to delete event image blob %s", name, exc_info=True)

    transaction.on_commit(_delete_blob)


# ── Portfolio cache invalidation ─────────────────────────────────────────────
#
# The public portfolio responses are cached (apps/events/public_views.py). The
# TTL alone would be enough for correctness, but not for the workflow: staff who
# tick "publish" and then check the site would see nothing for up to five
# minutes and reasonably conclude it was broken. These receivers make the change
# visible on the next request, leaving the TTL as a backstop for anything that
# writes without firing a signal (a bulk `queryset.update()`, a data migration).
#
# Wired for saves AND deletes, on all three models, because any of them can
# change a published page: the event carries the headline, the day carries the
# narrative, the image is the gallery.


def _slug_of(instance) -> str | None:
    """The event slug behind any of the three models, or None if it can't be
    reached (a cascade may already have removed the parent)."""
    try:
        if isinstance(instance, Event):
            return instance.slug
        if isinstance(instance, EventDay):
            return instance.owner.slug
        if isinstance(instance, EventImage):
            return instance.event.slug
    except (AttributeError, Event.DoesNotExist, EventDay.DoesNotExist):
        return None
    return None


@receiver(post_save, sender=Event)
@receiver(post_save, sender=EventDay)
@receiver(post_save, sender=EventImage)
@receiver(post_delete, sender=Event)
@receiver(post_delete, sender=EventDay)
@receiver(post_delete, sender=EventImage)
def invalidate_portfolio(sender, instance, **kwargs) -> None:
    from .public_views import invalidate_portfolio_cache

    # on_commit so a rolled-back transaction doesn't evict a still-correct
    # entry, and so the next reader repopulates from committed state rather than
    # racing the write it was triggered by.
    slug = _slug_of(instance)
    transaction.on_commit(lambda: invalidate_portfolio_cache(slug))
