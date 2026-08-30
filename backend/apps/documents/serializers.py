from rest_framework import serializers

from .models import Document


class DocumentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    uploaded_by_email = serializers.EmailField(source="uploaded_by.email", read_only=True)

    class Meta:
        model = Document
        fields = [
            "id",
            "category",
            "filename",
            "file_url",
            "file_size",
            "mime_type",
            "uploaded_by_email",
            "uploaded_at",
        ]

    def get_file_url(self, obj):
        request = self.context.get("request")
        if obj.file_path and request:
            from django.core.files.storage import default_storage
            url = default_storage.url(obj.file_path)
            return request.build_absolute_uri(url)
        return None
