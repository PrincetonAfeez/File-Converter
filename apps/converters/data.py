# "Pandas table converter (CSV/JSON/XLSX)."
from __future__ import annotations

import json
import shutil
from pathlib import Path

import ijson
import pandas as pd
from django.conf import settings

from .interface import (
    ConversionOptionSchema,
    ConversionResult,
    FormatPair,
    ResourceEstimate,
    SourceMetadata,
)

# Leading characters that spreadsheet apps (Excel/Sheets/LibreOffice) interpret as the
# start of a formula. Cells beginning with these are neutralized on CSV/XLSX output to
# prevent CSV formula injection (CWE-1236).
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")

# Rough bytes-per-row ceiling used to reject oversized JSON before parsing into memory.
_JSON_BYTES_PER_ROW_ESTIMATE = 512


def _escape_formula(value):
    if isinstance(value, str) and value and value[0] in _FORMULA_TRIGGERS:
        return "'" + value
    return value


def _harden_frame(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for col in frame.columns:
        if frame[col].dtype == object:
            frame[col] = frame[col].map(_escape_formula)
    frame.columns = [_escape_formula(c) for c in frame.columns]
    return frame


def _max_table_rows() -> int:
    return getattr(settings, "FILECONVERTER_MAX_TABLE_ROWS", 1_000_000)


def _json_byte_budget(max_rows: int) -> int:
    cap = getattr(settings, "FILECONVERTER_MAX_UPLOAD_BYTES", 50 * 1024 * 1024)
    return min(max_rows * _JSON_BYTES_PER_ROW_ESTIMATE, cap)


def _load_json_table(path: Path, *, max_rows: int) -> pd.DataFrame:
    """Load a JSON table with a row cap, streaming arrays to bound memory."""
    byte_size = path.stat().st_size
    if byte_size > _json_byte_budget(max_rows):
        raise ValueError(
            f"Input JSON exceeds the estimated size limit for {max_rows:,} rows."
        )
    with path.open("rb") as handle:
        preview = handle.read(1)
        handle.seek(0)
        if preview == b"[":
            rows = []
            for index, item in enumerate(ijson.items(handle, "item")):
                if index >= max_rows:
                    raise ValueError(
                        f"Input exceeds the {max_rows:,}-row limit for table conversions."
                    )
                rows.append(item)
            return pd.DataFrame(rows)
        payload = json.load(handle)
    if isinstance(payload, list):
        if len(payload) > max_rows:
            raise ValueError(
                f"Input exceeds the {max_rows:,}-row limit for table conversions."
            )
        return pd.DataFrame(payload)
    frame = pd.DataFrame(payload)
    if len(frame) > max_rows:
        raise ValueError(
            f"Input exceeds the {max_rows:,}-row limit for table conversions."
        )
    return frame


class DataTableConverter:
    converter_name = "pandas-table"
    converter_version = "1.0.0"
    progress_mode = "indeterminate"

    formats = ["csv", "json", "xlsx"]

    def supported_pairs(self) -> list[FormatPair]:
        return [
            FormatPair(source, target)
            for source in self.formats
            for target in self.formats
            if source != target
        ]

    def option_schema(self) -> ConversionOptionSchema:
        return ConversionOptionSchema(version="1")

    def estimate_cost(self, metadata: SourceMetadata, options: dict) -> ResourceEstimate:
        return ResourceEstimate(seconds=10, memory_mb=512)

    def probe(self, input_path: Path) -> SourceMetadata:
        fmt = input_path.suffix.lower().lstrip(".")
        rows = None
        if fmt == "csv":
            rows = max(sum(1 for _ in input_path.open("rb")) - 1, 0)
        return SourceMetadata(format=fmt, byte_size=input_path.stat().st_size, rows=rows)

    def validate_input(self, input_path: Path, metadata: SourceMetadata) -> None:
        fmt = metadata.format
        max_rows = _max_table_rows()
        if fmt == "csv":
            pd.read_csv(input_path, nrows=1)
        elif fmt == "xlsx":
            pd.read_excel(input_path, nrows=1)
        elif fmt == "json":
            _load_json_table(input_path, max_rows=max_rows).head(1)
        else:
            raise ValueError(f"Unsupported data source {fmt}")

    def convert(
        self,
        input_path: Path,
        output_path: Path,
        target_format: str,
        options: dict,
        progress_callback=None,
    ):
        source_format = input_path.suffix.lower().lstrip(".")
        if progress_callback:
            progress_callback(20, "Reading data")
        frame = self._read(input_path, source_format)
        if progress_callback:
            progress_callback(75, "Writing data")
        if target_format in {"csv", "xlsx"} and getattr(
            settings, "FILECONVERTER_SANITIZE_SPREADSHEET_FORMULAS", True
        ):
            frame = _harden_frame(frame)
        if target_format == "csv":
            frame.to_csv(output_path, index=False)
        elif target_format == "json":
            frame.to_json(output_path, orient="records", indent=2)
        elif target_format == "xlsx":
            frame.to_excel(output_path, index=False)
        else:
            raise ValueError(f"Unsupported data target {target_format}")
        return ConversionResult(
            output_format=target_format,
            byte_size=output_path.stat().st_size,
            metadata={"rows": len(frame), "columns": list(frame.columns)},
        )

    def validate_output(self, output_path: Path, result: ConversionResult) -> None:
        fmt = output_path.suffix.lower().lstrip(".")
        if fmt == "json":
            json.loads(output_path.read_text(encoding="utf-8"))
        else:
            self._read(output_path, fmt).head(1)

    def cleanup(self, work_dir: Path) -> None:
        shutil.rmtree(work_dir, ignore_errors=True)

    def _read(self, path: Path, fmt: str):
        max_rows = _max_table_rows()
        if fmt == "csv":
            frame = pd.read_csv(path, nrows=max_rows + 1)
        elif fmt == "json":
            frame = _load_json_table(path, max_rows=max_rows + 1)
            if len(frame) > max_rows:
                raise ValueError(
                    f"Input exceeds the {max_rows:,}-row limit for table conversions."
                )
            return frame
        elif fmt == "xlsx":
            frame = pd.read_excel(path, nrows=max_rows + 1)
        else:
            raise ValueError(f"Unsupported data source {fmt}")
        if len(frame) > max_rows:
            raise ValueError(
                f"Input exceeds the {max_rows:,}-row limit for table conversions."
            )
        return frame
