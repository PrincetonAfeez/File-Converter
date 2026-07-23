# "Django admin for conversion jobs and events."
from django.contrib import admin

from .models import ConversionBatch, ConversionJob, JobEvent


class JobEventInline(admin.TabularInline):
    model = JobEvent
    extra = 0
    readonly_fields = ("event_type", "message", "metadata", "created_at")


@admin.register(ConversionJob)
class ConversionJobAdmin(admin.ModelAdmin):
    list_display = (
        "original_display_filename",
        "source_format",
        "target_format",
        "status",
        "workspace",
        "claim_generation",
        "created_at",
    )
    list_filter = ("status", "source_format", "target_format", "created_at")
    search_fields = ("original_display_filename", "public_id", "input_checksum", "output_checksum")
    readonly_fields = ("public_id", "claim_generation", "created_at", "updated_at")
    inlines = [JobEventInline]


@admin.register(ConversionBatch)
class ConversionBatchAdmin(admin.ModelAdmin):
    list_display = ("public_id", "organization", "workspace", "status", "total_jobs", "created_at")
    readonly_fields = ("public_id", "created_at", "started_at", "finished_at")
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(JobEvent)
