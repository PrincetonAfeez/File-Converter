# "Tests for conversion progress reporting."
"""Tests for conversion progress reporting."""

from __future__ import annotations

import pytest
from django.core.cache import cache

from apps.conversions.models import ConversionJob
from apps.conversions.progress import ProgressReporter, get_cached_progress, progress_key


@pytest.mark.django_db
def test_progress_reporter_writes_cache(make_job):
    job, _user, _ws = make_job(status=ConversionJob.Status.PROCESSING, claim_generation=1)
    reporter = ProgressReporter(job.public_id)
    reporter(42, "Halfway")
    cached = cache.get(progress_key(job.public_id))
    assert cached["percent"] == 42
    assert cached["message"] == "Halfway"


@pytest.mark.django_db
def test_progress_reporter_throttles_small_updates(make_job):
    job, _user, _ws = make_job(status=ConversionJob.Status.PROCESSING, claim_generation=1)
    reporter = ProgressReporter(job.public_id, min_interval=999, min_percent_delta=50)
    reporter(10, "A")
    reporter(11, "B")
    cached = cache.get(progress_key(job.public_id))
    assert cached["percent"] == 10


@pytest.mark.django_db
def test_progress_reporter_persists_to_db(make_job):
    job, _user, _ws = make_job(status=ConversionJob.Status.PROCESSING, claim_generation=3)
    reporter = ProgressReporter(job.public_id, job_pk=job.pk, claim_generation=3)
    reporter(55, "Converting")
    job.refresh_from_db()
    assert job.progress_percent == 55
    assert "Converting" in job.progress_message


@pytest.mark.django_db
def test_get_cached_progress_falls_back_to_model(make_job):
    job, _user, _ws = make_job()
    job.progress_percent = 33
    job.progress_message = "From DB"
    job.save(update_fields=["progress_percent", "progress_message"])
    cache.delete(progress_key(job.public_id))
    progress = get_cached_progress(job)
    assert progress["percent"] == 33
    assert progress["message"] == "From DB"
