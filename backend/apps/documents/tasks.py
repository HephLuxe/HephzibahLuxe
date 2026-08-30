"""
apps/documents/tasks.py

cleanup_orphaned_documents_task — a scheduled wrapper around the
`cleanup_orphaned_documents` management command. Runs from cron group
`weekly_maintenance`.

The command itself is unchanged and remains the thing you run by hand (with
`--dry-run`) when you want to read the report before anything is deleted. It was
thoughtfully written, documents two real leak paths — a FileField replacement
never deletes the old blob, and a rollback-after-seed leaves an orphan blob —
and was scheduled nowhere, so every orphan it describes was accumulating in R2
and being billed as storage.
"""

import logging

from django.core.management import call_command

from apps.core.background import background_task

logger = logging.getLogger(__name__)


@background_task(name="documents.cleanup_orphaned")
def cleanup_orphaned_documents_task() -> None:
    """
    Delete orphaned Document registry rows and orphaned document_hub file blobs.

    Deletes for real — no `--dry-run`. The command's second pass walks R2 and
    removes blobs no live row references, which is the whole point of scheduling
    it, but it is also the reason this is admin-gated: if a future change to the
    document_hub path layout ever made the orphan test wrong, turning
    `documents_cleanup_orphaned` off in Scheduled Task Settings stops the
    deletions without a redeploy.
    """
    from apps.notifications.models import ScheduledTaskSettings

    if not ScheduledTaskSettings.is_task_enabled("documents_cleanup_orphaned"):
        return

    call_command("cleanup_orphaned_documents")
