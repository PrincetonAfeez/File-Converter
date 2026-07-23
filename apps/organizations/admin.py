# "Django admin for org and membership models."
from django.contrib import admin

from .models import Membership, Organization, Workspace, WorkspaceMembership


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "created_at")
    search_fields = ("name", "slug")


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "slug", "created_at")
    list_filter = ("organization",)
    search_fields = ("name", "slug", "organization__name")


admin.site.register(Membership)
admin.site.register(WorkspaceMembership)
