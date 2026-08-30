from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.portal.models import ClientPortal

from .serializers import DocumentSerializer


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_portal_documents(request):
    """
    Client: list documents belonging to their portal.
    Filterable by ?category=<category>
    """
    portal = get_object_or_404(ClientPortal, user=request.user)
    engagement = portal.active_engagement
    if not engagement:
        return Response([])
    docs = engagement.documents.all()

    category = request.query_params.get("category")
    if category:
        docs = docs.filter(category=category)

    serializer = DocumentSerializer(docs, many=True, context={"request": request})
    return Response(serializer.data)
