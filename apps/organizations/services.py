# "Workspace bootstrap and session helpers."
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Membership, Organization, Workspace, WorkspaceMembership


@transaction.atomic
def ensure_personal_workspace(user) -> Workspace | None:
    membership = (
        Membership.objects.select_related("organization")
        .filter(
            user=user,
            status=Membership.Status.ACTIVE,
            organization__status=Organization.Status.ACTIVE,
        )
        .order_by("created_at")
        .first()
    )
    if membership:
        workspace = membership.organization.workspaces.order_by("created_at").first()
        if workspace:
            return workspace

    if Membership.objects.filter(user=user).exists():
        return None

    base_name = user.get_full_name() or user.get_username() or "Personal"
    org = Organization.objects.create(name=f"{base_name}'s Organization")
    Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
    return Workspace.objects.create(organization=org, name="General")


def organization_is_active(organization: Organization) -> bool:
    return organization.status == Organization.Status.ACTIVE


def output_ttl_hours(organization: Organization) -> int:
    """Resolve per-org output retention; honor explicit 0 as immediate expiry."""
    ttl = organization.default_output_ttl_hours
    if ttl == 0:
        return 0
    if ttl:
        return ttl
    from django.conf import settings

    return settings.FILECONVERTER_OUTPUT_TTL_HOURS


def active_membership_org_ids(user):
    """Organization ids the user may access (active membership + active org)."""
    from .models import Membership

    return Membership.objects.filter(
        user=user,
        status=Membership.Status.ACTIVE,
        organization__status=Organization.Status.ACTIVE,
    ).values_list("organization_id", flat=True)


def user_has_login_access(user) -> bool:
    """True when the user belongs to at least one non-suspended organization."""
    from .models import Membership

    return Membership.objects.filter(
        user=user,
        status=Membership.Status.ACTIVE,
        organization__status=Organization.Status.ACTIVE,
    ).exists()


def accessible_workspaces(user) -> list[Workspace]:
    """Workspaces the user may use for uploads, excluding suspended orgs."""
    if not user.is_authenticated:
        return []
    if user.is_superuser:
        return list(
            Workspace.objects.select_related("organization").order_by(
                "organization__name", "name"
            )
        )
    workspaces: list[Workspace] = []
    memberships = Membership.objects.filter(
        user=user, status=Membership.Status.ACTIVE
    ).select_related("organization")
    for membership in memberships.order_by("organization__name", "created_at"):
        if not organization_is_active(membership.organization):
            continue
        for workspace in membership.organization.workspaces.order_by("name"):
            if user_can_access_workspace(user, workspace):
                workspaces.append(workspace)
    return workspaces


def get_active_workspace(request) -> Workspace | None:
    """Resolve the session-selected workspace or fall back to the personal workspace."""
    workspace_id = request.session.get("active_workspace_id")
    if workspace_id:
        workspace = (
            Workspace.objects.select_related("organization")
            .filter(public_id=workspace_id)
            .first()
        )
        if (
            workspace
            and organization_is_active(workspace.organization)
            and user_can_access_workspace(request.user, workspace)
        ):
            return workspace
    personal = ensure_personal_workspace(request.user)
    if personal is None:
        request.session.pop("active_workspace_id", None)
        return None
    request.session["active_workspace_id"] = str(personal.public_id)
    return personal


def set_active_workspace(request, workspace_public_id: str) -> Workspace | None:
    workspace = (
        Workspace.objects.select_related("organization")
        .filter(public_id=workspace_public_id)
        .first()
    )
    if workspace is None:
        return None
    if not organization_is_active(workspace.organization):
        return None
    if not user_can_access_workspace(request.user, workspace):
        return None
    request.session["active_workspace_id"] = str(workspace.public_id)
    return workspace


def user_can_access_workspace(user, workspace: Workspace) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    membership = Membership.objects.filter(
        user=user, organization=workspace.organization, status=Membership.Status.ACTIVE
    ).first()
    if membership is None:
        return False
    # Org owners/admins can reach every workspace in their org.
    if membership.role in {Membership.Role.OWNER, Membership.Role.ADMIN}:
        return True
    workspace_acl = WorkspaceMembership.objects.filter(workspace=workspace)
    if workspace_acl.exists():
        ws_membership = workspace_acl.filter(user=user).first()
        if ws_membership is None:
            return False
        return ws_membership.role != Membership.Role.AUDITOR
    return membership.role != Membership.Role.AUDITOR


def bootstrap_demo_user(username: str = "demo", password: str = "demo12345"):
    User = get_user_model()
    user, created = User.objects.get_or_create(username=username)
    if created:
        user.set_password(password)
        user.email = "demo@example.com"
        user.save(update_fields=["password", "email"])
    ensure_personal_workspace(user)
    return user
