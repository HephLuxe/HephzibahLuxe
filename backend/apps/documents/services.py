from django.contrib.contenttypes.models import ContentType
from .models import Document, DocumentCategory


def register_document(engagement, source_instance, file_path: str, category: str, uploaded_by=None, file_size=None, mime_type=""):
    """
    Create or update a Document record linking to an already-saved file path string.

    Usage:
        register_document(
            engagement=engagement,
            source_instance=prep_response,
            file_path=prep_response.file.name,
            category=DocumentCategory.PREP_UPLOAD,
            uploaded_by=request.user,
        )
    """
    content_type = ContentType.objects.get_for_model(source_instance)
    filename = file_path.split("/")[-1] if file_path else ""

    return Document.objects.update_or_create(
        content_type=content_type,
        object_id=source_instance.pk,
        defaults={
            "engagement": engagement,
            "uploaded_by": uploaded_by,
            "category": category,
            "file_path": file_path,
            "filename": filename,
            "file_size": file_size,
            "mime_type": mime_type,
        }
    )


def unregister_document(source_instance) -> int:
    """
    The inverse of register_document: remove the Document registry row(s) that
    pointed at a source object being deleted.

    Document links to its source through a *generic* FK, which Django cannot
    cascade — so without this, deleting the source (a PrepItemFileUpload, a
    receipt, ...) leaves a dangling Document row that still shows up in the
    client's document list. Callers (typically a post_delete signal on the
    source model) invoke this so the registry stays in step with reality.

    Deletes only the registry row, never the file blob — the caller owns the
    file and is better placed to remove it (and to defer that to on_commit).
    Safe to call when no row exists. Returns the number of Document rows deleted.
    """
    content_type = ContentType.objects.get_for_model(source_instance)
    deleted, _ = Document.objects.filter(
        content_type=content_type, object_id=str(source_instance.pk)
    ).delete()
    return deleted