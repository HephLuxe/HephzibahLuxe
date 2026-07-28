"""
apps/meetings/signals.py

Keeps the Document registry and file storage in step when a prep-item file
upload is deleted — through *any* path (staff deleting a field or prep item,
the meeting being deleted, or a field's type being changed, which wipes its
uploads). Wired up in MeetingsConfig.ready().

Why a signal rather than doing this inline in each delete site: a
PrepItemFileUpload is removed from several places, and cascade deletes
(field → uploads, item → fields → uploads, meeting → … → uploads) never pass
through view code at all. A post_delete receiver is the one hook every path
goes through. Registering it also forces Django off its "fast delete"
optimisation for this model, so the signal genuinely fires on cascades.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import PrepItemFileUpload

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=PrepItemFileUpload)
def cleanup_prep_upload(sender, instance: PrepItemFileUpload, **kwargs) -> None:
    # 1. Drop the Document registry row that pointed at this upload. This is a
    #    DB write, so it rides the same transaction as the delete — if that
    #    rolls back, so does this, keeping registry and uploads consistent.
    from apps.documents.services import unregister_document
    unregister_document(instance)

    # 2. Remove the physical blob. Storage is NOT transactional, so defer it to
    #    on_commit: if the surrounding transaction rolls back, the upload row is
    #    restored and we must NOT have already deleted its file (the very reason
    #    Django won't auto-delete FileField files). In autocommit the callback
    #    runs immediately after the delete commits.
    file = instance.file
    if file:
        def _delete_blob(name=file.name, storage=file.storage):
            try:
                storage.delete(name)
            except Exception:
                # A missing/already-gone blob shouldn't break anything; log and
                # move on rather than raise out of a post-commit hook.
                logger.warning("Failed to delete prep upload blob %s", name, exc_info=True)

        transaction.on_commit(_delete_blob)
