# "Dashboard, job detail, cancel, and download views."
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.converters.registry import UnsupportedFormatPair
from apps.organizations.permissions import user_may_cancel_job, user_may_download_job
from apps.organizations.services import (
    accessible_workspaces,
    get_active_workspace,
    organization_is_active,
    set_active_workspace,
    user_can_access_workspace,
)

from .forms import ConversionUploadForm, conversion_format_maps
from .models import ConversionJob
from .progress import get_cached_progress
from .services import request_cancel, submit_conversion_job

logger = logging.getLogger(__name__)


@login_required
def dashboard(request):
    if request.method == "POST" and request.POST.get("action") == "switch_workspace":
        if set_active_workspace(request, request.POST.get("workspace_id", "")):
            messages.success(request, "Workspace switched.")
        else:
            messages.error(request, "Could not switch to that workspace.")
        return redirect("dashboard")

    workspace = get_active_workspace(request)
    workspaces = accessible_workspaces(request.user)
    if workspace is None:
        messages.error(request, "No active organization is available for your account.")
    elif request.method == "POST":
        form = ConversionUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            pass
        else:
            try:
                job = submit_conversion_job(
                    user=request.user,
                    workspace=workspace,
                    uploaded_file=form.cleaned_data["file"],
                    target_format=form.cleaned_data["target_format"],
                    idempotency_key=form.cleaned_data["idempotency_key"],
                    options=form.option_payload(),
                )
                messages.success(request, "Conversion job queued.")
                return redirect("job_detail", public_id=job.public_id)
            except (ValidationError, ValueError, UnsupportedFormatPair) as exc:
                message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
                messages.error(request, message)
            except Exception:
                logger.exception("Unexpected error while submitting conversion job")
                messages.error(
                    request, "Something went wrong queuing your conversion. Please try again."
                )
    else:
        form = ConversionUploadForm()

    jobs = []
    format_targets, target_options = conversion_format_maps()
    if workspace is not None:
        jobs = (
            ConversionJob.objects.accessible_to(request.user)
            .filter(workspace=workspace)
            .select_related("output_file")[:8]
        )
    return render(
        request,
        "conversions/dashboard.html",
        {
            "form": form,
            "workspace": workspace,
            "workspaces": workspaces,
            "jobs": jobs,
            "format_targets": format_targets,
            "target_options": target_options,
        },
    )


JOB_LIST_PAGE_SIZE = 25


@login_required
def job_list(request):
    if request.method == "POST" and request.POST.get("action") == "switch_workspace":
        if set_active_workspace(request, request.POST.get("workspace_id", "")):
            messages.success(request, "Workspace switched.")
        else:
            messages.error(request, "Could not switch to that workspace.")
        return redirect("job_list")

    workspace = get_active_workspace(request)
    workspaces = accessible_workspaces(request.user)
    jobs = []
    page = None
    if workspace is not None:
        job_qs = (
            ConversionJob.objects.accessible_to(request.user)
            .filter(workspace=workspace)
            .select_related("output_file")
        )
        paginator = Paginator(job_qs, JOB_LIST_PAGE_SIZE)
        page = paginator.get_page(request.GET.get("page"))
        jobs = page.object_list
    return render(
        request,
        "conversions/job_list.html",
        {
            "page": page,
            "jobs": jobs,
            "workspace": workspace,
            "workspaces": workspaces,
        },
    )


@login_required
def job_detail(request, public_id):
    job = get_job_for_user(request.user, public_id)
    return render(
        request, "conversions/job_detail.html", {"job": job, "progress": get_cached_progress(job)}
    )


@login_required
def job_status(request, public_id):
    job = get_job_for_user(request.user, public_id)
    return render(
        request,
        "conversions/partials/job_status.html",
        {"job": job, "progress": get_cached_progress(job)},
    )


@login_required
def cancel_job(request, public_id):
    job = get_job_for_user(request.user, public_id)
    if not user_may_cancel_job(request.user, job):
        raise PermissionDenied("You do not have permission to cancel this job.")
    if request.method != "POST":
        return redirect("job_detail", public_id=job.public_id)
    was_terminal = job.is_terminal
    was_cancel_requested = job.cancel_requested
    request_cancel(job, actor=request.user)
    job.refresh_from_db()
    if was_terminal:
        messages.info(request, "This job is already finished.")
    elif job.status == ConversionJob.Status.CANCELLED and not was_cancel_requested:
        messages.success(request, "Job cancelled.")
    elif not was_cancel_requested:
        messages.success(request, "Cancellation requested.")
    else:
        messages.info(request, "Cancellation already requested.")
    return redirect("job_detail", public_id=job.public_id)


@login_required
def download_job(request, public_id):
    job = get_job_for_user(request.user, public_id)
    if not user_may_download_job(request.user, job):
        raise PermissionDenied("You do not have permission to download this output.")
    if not job.output_downloadable:
        raise Http404("Converted output is not available")
    file_handle = job.output_file.file.open("rb")
    response = FileResponse(
        file_handle,
        as_attachment=True,
        filename=job.output_file.original_name,
    )
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response["Pragma"] = "no-cache"
    return response


def get_job_for_user(user, public_id) -> ConversionJob:
    job = get_object_or_404(
        ConversionJob.objects.accessible_to(user).select_related(
            "workspace", "organization", "output_file"
        ),
        public_id=public_id,
    )
    if not user_can_access_workspace(user, job.workspace):
        raise PermissionDenied("You do not have access to this job")
    if not organization_is_active(job.organization):
        raise PermissionDenied("This organization is suspended")
    return job
