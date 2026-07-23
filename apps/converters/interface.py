# "Converter protocol and option schemas."
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

ProgressCallback = Callable[[int, str], None]


class TransientConversionError(Exception):
    """Raised by converters for failures that should be retried by the job runner."""


@dataclass(frozen=True)
class FormatPair:
    source: str
    target: str


@dataclass(frozen=True)
class OptionField:
    name: str
    type: Literal["string", "integer", "boolean", "choice"]
    label: str
    default: Any = None
    choices: list[str] = field(default_factory=list)
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True)
class ConversionOptionSchema:
    version: str
    fields: list[OptionField] = field(default_factory=list)

    def defaults(self) -> dict[str, Any]:
        return {field.name: field.default for field in self.fields if field.default is not None}

    def validate(self, options: dict[str, Any] | None) -> dict[str, Any]:
        raw = {**self.defaults(), **(options or {})}
        cleaned: dict[str, Any] = {}
        for field_def in self.fields:
            value = raw.get(field_def.name, field_def.default)
            if field_def.type == "integer":
                value = int(value)
                if field_def.minimum is not None and value < field_def.minimum:
                    raise ValueError(f"{field_def.name} must be >= {field_def.minimum}")
                if field_def.maximum is not None and value > field_def.maximum:
                    raise ValueError(f"{field_def.name} must be <= {field_def.maximum}")
            elif field_def.type == "boolean":
                value = value in {True, "true", "True", "1", "on", "yes"}
            elif field_def.type == "choice" and value not in field_def.choices:
                raise ValueError(f"{field_def.name} is not a supported choice")
            cleaned[field_def.name] = value
        return cleaned


@dataclass(frozen=True)
class ResourceEstimate:
    seconds: int = 10
    memory_mb: int = 256


@dataclass(frozen=True)
class SourceMetadata:
    format: str
    mime_type: str = ""
    byte_size: int = 0
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    rows: int | None = None


@dataclass(frozen=True)
class ConversionResult:
    output_format: str
    byte_size: int
    metadata: dict[str, Any] = field(default_factory=dict)


class Converter(Protocol):
    converter_name: str
    converter_version: str
    progress_mode: Literal["determinate", "indeterminate"]

    def supported_pairs(self) -> list[FormatPair]: ...
    def option_schema(self) -> ConversionOptionSchema: ...
    def estimate_cost(self, metadata: SourceMetadata, options: dict) -> ResourceEstimate: ...
    def probe(self, input_path: Path) -> SourceMetadata: ...
    def validate_input(self, input_path: Path, metadata: SourceMetadata) -> None: ...
    def convert(
        self,
        input_path: Path,
        output_path: Path,
        target_format: str,
        options: dict,
        progress_callback: ProgressCallback | None = None,
    ) -> ConversionResult: ...
    def validate_output(self, output_path: Path, result: ConversionResult) -> None: ...
    def cleanup(self, work_dir: Path) -> None: ...
