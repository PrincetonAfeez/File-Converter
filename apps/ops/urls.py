# "URL routes for ops endpoints."
from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("ready/", views.readiness, name="readiness"),
    path("metrics/", views.metrics, name="metrics"),
]
