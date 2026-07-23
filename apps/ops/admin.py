# "Django admin for ops models."
from django.contrib import admin

from .models import FeatureFlag


@admin.register(FeatureFlag)
class FeatureFlagAdmin(admin.ModelAdmin):
    list_display = ("name", "enabled", "description", "updated_at")
    list_filter = ("enabled",)
    search_fields = ("name", "description")
