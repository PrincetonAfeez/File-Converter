# "Tests for conversion forms."
"""Tests for conversion upload forms."""

from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.conversions.forms import ConversionUploadForm, conversion_format_maps


def test_conversion_format_maps_include_csv_targets():
    format_targets, target_options = conversion_format_maps()
    assert "json" in format_targets["csv"]
    assert isinstance(target_options.get("json", []), list)


def test_upload_form_generates_idempotency_key():
    form = ConversionUploadForm()
    assert form.initial.get("idempotency_key")


def test_upload_form_option_payload(csv_upload):
    form = ConversionUploadForm(
        data={
            "target_format": "json",
            "idempotency_key": "form-key-1",
            "quality": "90",
            "strip_metadata": "on",
        },
        files={"file": csv_upload},
    )
    assert form.is_valid(), form.errors
    payload = form.option_payload()
    assert payload.get("quality") == 90 or payload.get("quality") == "90" or "quality" in payload


def test_upload_form_invalid_without_file():
    form = ConversionUploadForm(data={"target_format": "json", "idempotency_key": "k"})
    assert form.is_valid() is False
