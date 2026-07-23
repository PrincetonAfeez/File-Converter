# "Django admin for FileBlob."
from django.contrib import admin

from .models import FileBlob


@admin.register(FileBlob)
class FileBlobAdmin(admin.ModelAdmin):
    list_display = ("original_name", "kind", "workspace", "byte_size", "created_at", "deleted_at")
    list_filter = ("kind", "created_at")
    search_fields = ("original_name", "sha256", "public_id")
