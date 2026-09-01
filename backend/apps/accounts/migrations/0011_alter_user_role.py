"""Adds the `developer` role and widens `role` to 20 characters.

Schema only, and deliberately so — there is no data migration promoting anyone.
The developer role is granted by ``settings.PLATFORM_DEVELOPER_EMAILS`` and
materialised by ``manage.py ensure_developer``, not by version control; baking an
email address into a frozen migration is exactly what that design avoids. See
apps/accounts/developers.py.

Both operations are safe on a live table: adding a choice is a Python-level
change Postgres never sees, and widening a varchar is a metadata-only ALTER that
takes no rewrite and no long lock.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_user_created_by_user_last_updated_by'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(choices=[('client', 'Client'), ('staff', 'Staff'), ('admin', 'Admin'), ('developer', 'Developer')], default='client', help_text='Developer is not assignable here — it mirrors the PLATFORM_DEVELOPER_EMAILS deployment setting.', max_length=20),
        ),
    ]
