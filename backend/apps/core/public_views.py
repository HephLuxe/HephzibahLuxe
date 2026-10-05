"""Anonymous reads for the decorative homepage strip.

`image` is serialized by DRF's ImageField exactly like the portfolio's
``PublicEventImageSerializer``: the storage URL passed through
``request.build_absolute_uri``. HomeStripImage.image lives on the PUBLIC media
storage (``select_public_media_storage``), so on prod that URL is already an
absolute, unsigned R2 custom-domain URL and passes through unchanged; locally
(USE_LOCAL_MEDIA) the relative ``/media/...`` path is made absolute.
"""

from rest_framework import serializers
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.response import Response

from .models import HomeStripImage
from .throttling import PortfolioRateThrottle


class PublicHomeStripImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = HomeStripImage
        fields = ["image", "alt_text", "sort_order"]


@api_view(["GET"])
@authentication_classes([])
@permission_classes([])
@throttle_classes([PortfolioRateThrottle])
def home_strip(request):
    return Response(
        PublicHomeStripImageSerializer(
            HomeStripImage.objects.filter(is_published=True),
            many=True, context={"request": request},
        ).data
    )
