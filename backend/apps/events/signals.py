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
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import EventImage

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
