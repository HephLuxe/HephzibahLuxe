"""Import the six decorative images from Portfolio.tsx at 784b6dc."""

from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image

from apps.core.models import HomeStripImage

# Paths and order verified against git show 784b6dc:frontend/components/sections/Portfolio.tsx.
ASSETS = (
    "images/portfoliopage/portfoliosix.jpg",
    "images/hero/herofours.jpg",
    "images/portfoliopage/portfoliofive.jpg",
    "images/portfoliopage/portfoliotwo.jpg",
    "images/portfoliopage/portfolioeight.jpg",
    "images/portfoliopage/portfolionine.jpg",
)


class Command(BaseCommand):
    help = "Import the original six homepage images; dry-run unless --apply is supplied."

    def add_arguments(self, parser):
        parser.add_argument("--source-root", type=Path, default=settings.BASE_DIR.parent / "frontend" / "public")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true")
        mode.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        # Read and validate the entire batch before any database or storage writes.
        validated = []
        for asset in ASSETS:
            path = options["source_root"] / asset
            try:
                content = path.read_bytes()
                with Image.open(BytesIO(content)) as image:
                    image.verify()
            except (OSError, ValueError, SyntaxError) as exc:
                raise CommandError(f"Invalid image {path}: {exc}") from exc
            validated.append((asset, content))

        created = 0
        for order, (asset, content) in enumerate(validated):
            key = f"home-strip:{asset}"
            if HomeStripImage.objects.filter(import_key=key).exists():
                self.stdout.write(f"Preserved {asset}")
                continue
            if not options["apply"]:
                self.stdout.write(f"Would create {asset} (sort_order={order})")
                continue
            with transaction.atomic():
                _, was_created = HomeStripImage.objects.get_or_create(
                    import_key=key,
                    defaults={
                        "image": ContentFile(content, name=Path(asset).name),
                        "alt_text": "",
                        "sort_order": order,
                        "is_published": True,
                    },
                )
            created += was_created
        self.stdout.write(f"Created {created} images." if options["apply"] else "Dry run; no writes.")
