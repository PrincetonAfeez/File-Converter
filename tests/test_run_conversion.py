# "Tests for run_conversion error paths."
"""Deep tests for run_conversion error handling and terminal transitions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apps.conversions.models import ConversionJob
from apps.conversions.services import JobFenced, run_conversion
from apps.converters.interface import ConversionResult, SourceMetadata, TransientConversionError
from apps.converters.registry import UnsupportedFormatPair
from apps.files.models import FileBlob


class _FakeConverter:
    converter_name = "fake-converter"
    converter_version = "1.0.0"

    def __init__(self, *, on_convert=None, on_validate_output=None):
        self._on_convert = on_convert
        self._on_validate_output = on_validate_output
        self.cleaned = []

    def probe(self, input_path: Path) -> SourceMetadata:
        return SourceMetadata(format="csv", byte_size=input_path.stat().st_size, rows=1)

    def validate_input(self, input_path: Path, metadata: SourceMetadata) -> None:
        return None

    def convert(
        self,
        input_path: Path,
        output_path: Path,
        target_format: str,
        options: dict,
        progress_callback=None,
    ):
        if self._on_convert:
            self._on_convert(input_path, output_path, progress_callback)
        output_path.write_text('{"ok": true}', encoding="utf-8")
        return ConversionResult(output_format=target_format, byte_size=output_path.stat().st_size)

    def validate_output(self, output_path: Path, result: ConversionResult) -> None:
        if self._on_validate_output:
            self._on_validate_output()

    def cleanup(self, work_dir: Path) -> None:
        self.cleaned.append(work_dir)


@pytest.fixture
def fake_converter():
    converter = _FakeConverter()

    def _get(*_args, **_kwargs):
        return converter

    with patch("apps.conversions.services.registry.get", side_effect=_get):
        yield converter


@pytest.mark.django_db
def test_run_conversion_success_marks_done(settings, make_job, fake_converter):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)
    run_conversion(job.pk)
    job.refresh_from_db()
    assert job.status == ConversionJob.Status.DONE
    assert job.progress_percent == 100
    assert job.output_file_id is not None
    assert fake_converter.cleaned


@pytest.mark.django_db
def test_run_conversion_job_fenced_aborts_silently(settings, make_job, fake_converter):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)

    def fence_during_convert(_input_path, _output_path, _progress_callback):
        ConversionJob.objects.filter(pk=job.pk).update(claim_generation=999)

    fake_converter._on_convert = fence_during_convert
    run_conversion(job.pk)
    job.refresh_from_db()
    assert job.status == ConversionJob.Status.PROCESSING
    assert job.claim_generation == 999


@pytest.mark.django_db
def test_run_conversion_cancelled_during_run(settings, make_job, fake_converter):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)

    def cancel_during_convert(_input_path, _output_path, _progress_callback):
        ConversionJob.objects.filter(pk=job.pk).update(cancel_requested=True)

    fake_converter._on_convert = cancel_during_convert
    run_conversion(job.pk)
    job.refresh_from_db()
    assert job.status == ConversionJob.Status.CANCELLED


@pytest.mark.django_db(transaction=True)
def test_run_conversion_transient_error_schedules_retry(settings, make_job, django_capture_on_commit_callbacks):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.FILECONVERTER_MAX_ATTEMPTS = 3
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)

    failing = _FakeConverter()

    def boom(*_args, **_kwargs):
        raise TransientConversionError("LibreOffice is busy")

    failing.convert = boom  # type: ignore[method-assign]

    with patch("apps.conversions.services.registry.get", return_value=failing):
        with patch("apps.conversions.tasks.process_conversion_job") as retry_task:
            retry_task.apply_async = MagicMock()
            with django_capture_on_commit_callbacks(execute=True):
                run_conversion(job.pk)

    job.refresh_from_db()
    assert job.status == ConversionJob.Status.RETRYING
    retry_task.apply_async.assert_called_once()


@pytest.mark.django_db
def test_run_conversion_transient_exhausted_emits_dead_letter(settings, make_job):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.FILECONVERTER_MAX_ATTEMPTS = 1
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)

    failing = _FakeConverter()

    def boom(*_args, **_kwargs):
        raise TransientConversionError("still failing")

    failing.convert = boom  # type: ignore[method-assign]

    with patch("apps.conversions.services.registry.get", return_value=failing):
        run_conversion(job.pk)

    job.refresh_from_db()
    assert job.status == ConversionJob.Status.FAILED
    assert job.events.filter(event_type="job.dead_letter").exists()


@pytest.mark.django_db
def test_run_conversion_permanent_failure(settings, make_job):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)

    failing = _FakeConverter()

    def boom(*_args, **_kwargs):
        raise ValueError("corrupt input")

    failing.convert = boom  # type: ignore[method-assign]

    with patch("apps.conversions.services.registry.get", return_value=failing):
        run_conversion(job.pk)

    job.refresh_from_db()
    assert job.status == ConversionJob.Status.FAILED
    assert job.internal_error_code == "ValueError"
    assert not job.events.filter(event_type="job.dead_letter").exists()


@pytest.mark.django_db
def test_run_conversion_unsupported_pair(settings, make_job):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)

    with patch(
        "apps.conversions.services.registry.get",
        side_effect=UnsupportedFormatPair("nope", "nope"),
    ):
        run_conversion(job.pk)

    job.refresh_from_db()
    assert job.status == ConversionJob.Status.FAILED
    assert job.internal_error_code == "unsupported_pair"


@pytest.mark.django_db
def test_run_conversion_promotion_fenced_discards_output_blob(settings, make_job, fake_converter):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)

    with patch(
        "apps.conversions.services.promote_output",
        side_effect=JobFenced("Output promotion was fenced"),
    ):
        run_conversion(job.pk)

    job.refresh_from_db()
    assert job.status == ConversionJob.Status.PROCESSING
    assert job.output_file_id is None
    assert FileBlob.objects.filter(kind=FileBlob.Kind.OUTPUT, workspace=job.workspace).count() == 0
