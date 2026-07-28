"""
apps/core/models.py

Shared abstract base models. Concrete models across the project inherit these
instead of re-declaring the same fields. Both are ``abstract = True`` so they
create no tables and no migrations of their own.

  TimestampedModel     — created_at / updated_at on every row.
  UUIDPrimaryKeyModel  — UUID primary key for anything exposed in a URL or
                          returned to a client (never expose enumerable PKs).
  AttributedModel      — created_by / last_updated_by: WHO made the row and who
                          touched it last. Pairs with TimestampedModel to give
                          the full who+when quartet.

New apps (reminders, notifications, the document hub) should build on these.
Existing models are migrated onto them deliberately in a later phase to avoid
churn — see HEPHZIBAH_LUXE_AUDIT_AND_PLAN.md §4.5 / Part 8 Phase 6.
"""

import uuid

from django.conf import settings
from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDPrimaryKeyModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class UUIDTimestampedModel(UUIDPrimaryKeyModel, TimestampedModel):
    """Convenience base for the common case: UUID PK + timestamps."""

    class Meta:
        abstract = True


class AttributedModel(models.Model):
    """
    Who created a row and who last changed it — the "who" half of the audit
    quartet (created_at/updated_at from TimestampedModel supply the "when").

    Last-writer-wins: last_updated_by is overwritten on every edit, so this is
    attribution, not a full history. Both are SET_NULL so deleting a staff
    account never cascades away the records they touched — the row survives with
    the field nulled and the display name falls back to "".

    editable=False: these are system-set from request.user (see
    core.utils.save_with_attribution / stamp_attribution). Staff never type them,
    which also means admins must list them in readonly_fields, never in
    raw_id_fields or an editable fieldset.

    The %(app_label)s_%(class)s_* related names keep reverse accessors unique
    across the many models that inherit this.
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        editable=False, related_name="%(app_label)s_%(class)s_created",
    )
    last_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        editable=False, related_name="%(app_label)s_%(class)s_updated",
    )

    class Meta:
        abstract = True
