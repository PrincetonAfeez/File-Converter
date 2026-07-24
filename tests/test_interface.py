# "Tests for converter interface and schemas."
"""Tests for apps.converters.interface and registry."""

from __future__ import annotations

import pytest

from apps.converters.interface import (
    ConversionOptionSchema,
    FormatPair,
    OptionField,
    SourceMetadata,
    TransientConversionError,
)
from apps.converters.registry import ConverterRegistry, UnsupportedFormatPair


def test_option_schema_validate_integer_bounds():
    schema = ConversionOptionSchema(
        version="1",
        fields=[OptionField("quality", "integer", "Q", default=50, minimum=1, maximum=100)],
    )
    assert schema.validate({"quality": 80})["quality"] == 80
    with pytest.raises(ValueError, match=">="):
        schema.validate({"quality": 0})


def test_option_schema_validate_boolean_and_choice():
    schema = ConversionOptionSchema(
        version="1",
        fields=[
            OptionField("strip", "boolean", "Strip", default=False),
            OptionField("bitrate", "choice", "BR", default="128k", choices=["128k", "192k"]),
        ],
    )
    cleaned = schema.validate({"strip": "yes", "bitrate": "192k"})
    assert cleaned["strip"] is True
    assert cleaned["bitrate"] == "192k"
    with pytest.raises(ValueError, match="choice"):
        schema.validate({"bitrate": "999k"})


def test_option_schema_defaults():
    schema = ConversionOptionSchema(
        version="1",
        fields=[OptionField("quality", "integer", "Q", default=88, minimum=1, maximum=100)],
    )
    assert schema.defaults() == {"quality": 88}
    assert schema.validate({})["quality"] == 88


def test_transient_conversion_error_is_exception():
    assert issubclass(TransientConversionError, Exception)


def test_registry_duplicate_registration_raises():
    from apps.converters.images import PillowImageConverter

    reg = ConverterRegistry()
    reg.register(PillowImageConverter())
    with pytest.raises(ValueError, match="Duplicate"):
        reg.register(PillowImageConverter())


def test_registry_unsupported_pair():
    reg = ConverterRegistry()
    with pytest.raises(UnsupportedFormatPair):
        reg.get("nope", "nope")


def test_registry_all_pairs_sorted():
    from apps.converters.registry import registry

    pairs = registry.all_pairs()
    assert pairs == sorted(pairs, key=lambda p: (p.source, p.target))


def test_format_pair_frozen():
    pair = FormatPair("csv", "json")
    assert pair.source == "csv"
    assert pair.target == "json"
