# "Tests for LibreOffice and FFmpeg converters."
"""Tests for document and media converters (mocked subprocess)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apps.converters.documents import LibreOfficePdfConverter
from apps.converters.interface import TransientConversionError
from apps.converters.media import FfmpegMediaConverter


def test_libreoffice_rejects_empty_document(tmp_path):
    converter = LibreOfficePdfConverter()
    empty = tmp_path / "empty.docx"
    empty.write_bytes(b"")
    metadata = converter.probe(empty)
    with pytest.raises(ValueError, match="empty"):
        converter.validate_input(empty, metadata)


def test_libreoffice_missing_binary_raises_transient(tmp_path):
    converter = LibreOfficePdfConverter()
    src = tmp_path / "in.docx"
    src.write_bytes(b"fake")
    out = tmp_path / "out.pdf"
    with patch("apps.converters.documents.shutil.which", return_value=None):
        with pytest.raises(TransientConversionError, match="not installed"):
            converter.convert(src, out, "pdf", {})


def test_libreoffice_uses_file_uri(tmp_path):
    converter = LibreOfficePdfConverter()
    src = tmp_path / "in.docx"
    src.write_bytes(b"fake")
    out = tmp_path / "out.pdf"
    produced = tmp_path / "in.pdf"
    produced.write_bytes(b"%PDF-1.4\n")

    with patch("apps.converters.documents.shutil.which", return_value="/usr/bin/soffice"):
        with patch("apps.converters.documents.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0)
            converter.convert(src, out, "pdf", {})
            command = run.call_args[0][0]
            env_arg = next(arg for arg in command if arg.startswith("-env:UserInstallation="))
            assert env_arg.startswith("-env:UserInstallation=file://")


def test_libreoffice_process_error_is_transient(tmp_path):
    converter = LibreOfficePdfConverter()
    src = tmp_path / "in.docx"
    src.write_bytes(b"fake")
    out = tmp_path / "out.pdf"
    with patch("apps.converters.documents.shutil.which", return_value="/usr/bin/soffice"):
        with patch(
            "apps.converters.documents.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "soffice"),
        ):
            with pytest.raises(TransientConversionError):
                converter.convert(src, out, "pdf", {})


def test_ffmpeg_missing_raises_transient(tmp_path):
    converter = FfmpegMediaConverter()
    src = tmp_path / "in.wav"
    src.write_bytes(b"RIFF")
    out = tmp_path / "out.mp3"
    with patch("apps.converters.media.shutil.which", return_value=None):
        with pytest.raises(TransientConversionError, match="not installed"):
            converter.convert(src, out, "mp3", {})


def test_ffmpeg_adds_vn_for_audio(tmp_path):
    converter = FfmpegMediaConverter()
    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00")
    out = tmp_path / "out.mp3"

    mock_process = MagicMock()
    mock_process.stderr = iter([])
    mock_process.wait.return_value = 0

    with patch("apps.converters.media.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("apps.converters.media.subprocess.Popen", return_value=mock_process) as popen:
            out.write_bytes(b"mp3")
            with patch.object(Path, "stat", return_value=MagicMock(st_size=3)):
                converter.convert(src, out, "mp3", {"audio_bitrate": "192k"})
            command = popen.call_args[0][0]
            assert "-vn" in command


def test_ffmpeg_transient_stderr(tmp_path):
    converter = FfmpegMediaConverter()
    src = tmp_path / "in.wav"
    src.write_bytes(b"RIFF")
    out = tmp_path / "out.mp3"

    mock_process = MagicMock()
    mock_process.stderr = iter(["Resource temporarily unavailable\n"])
    mock_process.wait.return_value = 1

    with patch("apps.converters.media.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("apps.converters.media.subprocess.Popen", return_value=mock_process):
            with pytest.raises(TransientConversionError):
                converter.convert(src, out, "mp3", {})
