# "Tests for file upload utilities."
"""Tests for apps.files.utils."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

from apps.files.utils import (
    client_ip_from_request,
    detect_mime,
    extension_for_path,
    sanitize_filename,
    sha256_file,
    sha256_upload,
    validate_content_type,
    validate_upload_size,
)


def test_sanitize_filename_strips_paths_and_unsafe_chars():
    assert sanitize_filename(r"..\..\etc\passwd") == "passwd"
    assert sanitize_filename("folder/sub/file.csv") == "file.csv"
    assert sanitize_filename("") == "upload"
    assert len(sanitize_filename("a" * 300)) <= 240


def test_extension_for_path():
    assert extension_for_path("report.CSV") == "csv"


def test_validate_upload_size_rejects_empty():
    upload = SimpleUploadedFile("empty.csv", b"")
    with pytest.raises(ValueError, match="empty"):
        validate_upload_size(upload)


def test_validate_upload_size_rejects_oversize(settings):
    settings.FILECONVERTER_MAX_UPLOAD_BYTES = 10
    upload = SimpleUploadedFile("big.csv", b"x" * 20)
    with pytest.raises(ValueError, match="limit"):
        validate_upload_size(upload)


def test_sha256_upload_and_file_match(tmp_path):
    data = b"checksum-me\n"
    upload = SimpleUploadedFile("d.csv", data)
    path = tmp_path / "d.csv"
    path.write_bytes(data)
    assert sha256_upload(upload) == sha256_file(path)
    assert sha256_upload(upload) == hashlib.sha256(data).hexdigest()


def test_validate_content_type_accepts_matching_prefix():
    validate_content_type("csv", "text/plain")
    validate_content_type("png", "image/png")


def test_validate_content_type_rejects_mismatch():
    with pytest.raises(ValueError, match="does not match"):
        validate_content_type("csv", "image/png")


def test_validate_content_type_allows_unknown_extension():
    validate_content_type("xyz", "application/octet-stream")


def test_client_ip_from_request_remote_addr(settings):
    settings.FILECONVERTER_TRUSTED_PROXY_COUNT = 0
    request = RequestFactory().get("/")
    request.META["REMOTE_ADDR"] = "203.0.113.10"
    assert client_ip_from_request(request) == "203.0.113.10"


def test_client_ip_from_request_x_forwarded_for(settings):
    settings.FILECONVERTER_TRUSTED_PROXY_COUNT = 1
    request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="203.0.113.5, 10.0.0.1")
    request.META["REMOTE_ADDR"] = "10.0.0.1"
    assert client_ip_from_request(request) == "203.0.113.5"


def test_detect_mime_returns_string_for_real_file(tmp_path):
    path = tmp_path / "plain.txt"
    path.write_text("hello", encoding="utf-8")
    result = detect_mime(path)
    assert isinstance(result, str)
