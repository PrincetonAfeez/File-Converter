# "Workspace and job ACL helpers."
from __future__ import annotations

from .models import Membership, Workspace


def get_org_membership(user, organization) -> Membership | None:
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return None
    return Membership.objects.filter(
        user=user,
        organization=organization,
        status=Membership.Status.ACTIVE,
    ).first()


def user_may_write_workspace(user, workspace: Workspace) -> bool:
    """Upload and other mutating workspace actions."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    membership = get_org_membership(user, workspace.organization)
    if membership is None:
        return False
    if membership.role == Membership.Role.AUDITOR:
        return False
    if membership.role in {Membership.Role.OWNER, Membership.Role.ADMIN}:
        return True
    workspace_acl = workspace.memberships.filter(user=user).first()
    if workspace.memberships.exists():
        if workspace_acl is None:
            return False
        return workspace_acl.role != Membership.Role.AUDITOR
    return True


def user_may_cancel_job(user, job) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if job.owner_id == user.pk:
        return True
    membership = get_org_membership(user, job.organization)
    if membership is None:
        return False
    if membership.role == Membership.Role.AUDITOR:
        return False
    return membership.role in {Membership.Role.OWNER, Membership.Role.ADMIN}


def user_may_download_job(user, job) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if job.owner_id == user.pk:
        return True
    membership = get_org_membership(user, job.organization)
    if membership is None:
        return False
    return membership.role in {
        Membership.Role.OWNER,
        Membership.Role.ADMIN,
        Membership.Role.AUDITOR,
    }
