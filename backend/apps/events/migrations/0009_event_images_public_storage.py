# Route the display-image fields through the public-media storage selector
# (apps/core/storages.py). Storage is a callable, so this records only the
# reference — the actual backend is resolved from settings at boot and flipping
# USE_R2_STORAGE / configuring a public bucket later needs no further migration.

import apps.core.storages
import apps.core.utils
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0008_event_created_by_eventday_created_by_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='event',
            name='featured_image',
            field=models.ImageField(blank=True, max_length=500, null=True, storage=apps.core.storages.select_public_media_storage, upload_to=apps.core.utils.event_cover_upload_path),
        ),
        migrations.AlterField(
            model_name='eventday',
            name='event_images',
            field=models.ImageField(blank=True, max_length=500, null=True, storage=apps.core.storages.select_public_media_storage, upload_to=apps.core.utils.event_image_upload_path),
        ),
    ]
