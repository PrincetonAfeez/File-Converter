# "Tests for Pillow and data table converters."
"""Tests for Pillow and data table converters."""

from __future__ import annotations

import json
from io import BytesIO

import pytest
from django.conf import settings
from PIL import Image

from apps.converters.data import DataTableConverter, _load_json_table
from apps.converters.images import PillowImageConverter


@pytest.fixture
def png_path(tmp_path):
    path = tmp_path / "in.png"
    Image.new("RGB", (8, 8), color=(255, 0, 0)).save(path, "PNG")
    return path


def test_pillow_probe_and_convert(png_path, tmp_path):
    converter = PillowImageConverter()
    metadata = converter.probe(png_path)
    assert metadata.width == 8
    converter.validate_input(png_path, metadata)
    out = tmp_path / "out.jpg"
    result = converter.convert(png_path, out, "jpg", {"quality": 90, "strip_metadata": True})
    assert result.output_format == "jpg"
    converter.validate_output(out, result)


def test_pillow_rejects_corrupt_image(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not-an-image")
    converter = PillowImageConverter()
    with pytest.raises((ValueError, Image.UnidentifiedImageError)):
        converter.probe(bad)


def test_data_table_csv_to_json(csv_bytes, tmp_path):
    src = tmp_path / "in.csv"
    src.write_bytes(csv_bytes)
    out = tmp_path / "out.json"
    converter = DataTableConverter()
    meta = converter.probe(src)
    converter.validate_input(src, meta)
    result = converter.convert(src, out, "json", {})
    assert result.metadata["rows"] == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload[0]["name"] == "alpha"
    assert payload[0]["value"] == 1


def test_load_json_table_row_cap(tmp_path, settings):
    settings.FILECONVERTER_MAX_TABLE_ROWS = 2
    path = tmp_path / "big.json"
    path.write_text(json.dumps([{"i": i} for i in range(5)]), encoding="utf-8")
    with pytest.raises(ValueError, match="row limit"):
        _load_json_table(path, max_rows=2)


def test_data_table_formula_hardening_on_csv(tmp_path, settings):
    settings.FILECONVERTER_SANITIZE_SPREADSHEET_FORMULAS = True
    src = tmp_path / "evil.csv"
    src.write_text("name\n=cmd|'/c calc'!A0\n", encoding="utf-8")
    out = tmp_path / "out.csv"
    converter = DataTableConverter()
    converter.convert(src, out, "csv", {})
    text = out.read_text(encoding="utf-8")
    assert text.splitlines()[1].startswith("'=")
