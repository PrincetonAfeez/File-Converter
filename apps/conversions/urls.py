# "URL routes for conversion views."
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("jobs/", views.job_list, name="job_list"),
    path("jobs/<uuid:public_id>/", views.job_detail, name="job_detail"),
    path("jobs/<uuid:public_id>/status/", views.job_status, name="job_status"),
    path("jobs/<uuid:public_id>/cancel/", views.cancel_job, name="cancel_job"),
    path("jobs/<uuid:public_id>/download/", views.download_job, name="download_job"),
]
