# "Tests for converter registry lookups."
from apps.converters.registry import registry, UnsupportedFormatPair

import pytest


def test_registry_resolves_image_converter():
    converter = registry.get("png", "jpg")

    assert converter.converter_name == "pillow-image"


def test_registry_exposes_targets():
    targets = registry.targets_for_source("csv")

    assert "json" in targets
    assert "xlsx" in targets


def test_registry_resolves_all_builtin_families():
    assert registry.get("csv", "json").converter_name == "pandas-table"
    assert registry.get("docx", "pdf").converter_name == "libreoffice-pdf"
    assert registry.get("wav", "mp3").converter_name == "ffmpeg-media"


def test_registry_raises_unsupported_pair():
    with pytest.raises(UnsupportedFormatPair):
        registry.get("zzz", "qqq")
