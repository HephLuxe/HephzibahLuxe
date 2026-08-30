"""
Store password-reset codes hashed, and count verify attempts against them.

Two changes to `PasswordResetToken`, both security-motivated:

  * `code` (plaintext, 6 digits) -> `code_hash` (PBKDF2 via `make_password`).
    These rows held a live reset code in the clear next to the user it belonged
    to and the IP that requested it, and nothing deleted them. A bare digest
    would not have helped — a six-digit space is 10^6, enumerable in under a
    second — so this uses the password hasher, with its per-row salt and
    iteration count.

  * `attempt_count`, which is what makes the 30-minute code TTL safe. Before it,
    a code stayed guessable for its whole window and the only ceiling was the
    per-IP verify limits.

**Existing unused tokens are invalidated rather than migrated.** Their plaintext
is deliberately not re-encoded into a hash: the point of this change is that the
plaintext stops existing. Every affected code would have expired within 30
minutes anyway, and the cost is that anyone mid-reset requests a new one.

The index on `('code', 'is_used')` goes with the column — a salted hash cannot be
looked up by value, which is why `utils.verify_reset_code` now fetches the user's
outstanding token and checks against it. The remaining
`('user', 'is_used', 'expires_at')` index is exactly that lookup.
"""

from django.db import migrations, models


def invalidate_outstanding_tokens(apps, schema_editor):
    """Kill every unused token while `code` still exists, so no row is left
    holding a code that can no longer be verified."""
    from django.utils import timezone

    PasswordResetToken = apps.get_model('accounts', 'PasswordResetToken')
    PasswordResetToken.objects.filter(is_used=False).update(
        is_used=True, used_at=timezone.now()
    )


def noop(apps, schema_editor):
    """Irreversible: the plaintext codes are gone, which is the point."""


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_user_receives_inquiry_alerts'),
    ]

    operations = [
        migrations.RunPython(invalidate_outstanding_tokens, noop),
        migrations.RemoveIndex(
            model_name='passwordresettoken',
            name='accounts_pa_code_fb22d0_idx',
        ),
        migrations.RemoveField(
            model_name='passwordresettoken',
            name='code',
        ),
        migrations.AddField(
            model_name='passwordresettoken',
            name='attempt_count',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='passwordresettoken',
            name='code_hash',
            field=models.CharField(default='', max_length=128),
            preserve_default=False,
        ),
    ]
