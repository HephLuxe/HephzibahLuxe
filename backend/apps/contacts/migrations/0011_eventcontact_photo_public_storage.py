# Route EventContact.photo through the public-media storage selector
# (apps/core/storages.py) — see events/0009 for the rationale.

import apps.core.storages
import apps.core.utils
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0010_eventcontact_created_by_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='eventcontact',
            name='photo',
            field=models.ImageField(blank=True, max_length=500, storage=apps.core.storages.select_public_media_storage, upload_to=apps.core.utils.contact_photo_upload_path),
        ),
    ]
