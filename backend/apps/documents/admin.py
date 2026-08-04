from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ["filename", "category", "engagement", "content_type", "object_id", "file_size", "uploaded_by", "uploaded_at"]
    list_filter = ["category", "content_type"]
    search_fields = ["filename", "file_path", "engagement__portal__user__email"]
    raw_id_fields = ["engagement", "uploaded_by"]
    readonly_fields = ["uploaded_at", "content_type", "object_id"]
    ordering = ["-uploaded_at"]
