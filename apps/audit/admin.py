# "Django admin registration for audit models."
from django.contrib import admin

from .models import AuditEvent, OutboxEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "organization", "actor", "object_type", "created_at")
    list_filter = ("event_type", "created_at")
    search_fields = ("message", "object_id", "public_id")


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "organization", "idempotency_key", "delivered_at", "created_at")
    list_filter = ("event_type", "delivered_at")
