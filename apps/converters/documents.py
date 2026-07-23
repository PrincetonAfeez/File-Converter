# "LibreOffice document-to-PDF converter."
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .interface import (
    ConversionOptionSchema,
    ConversionResult,
    FormatPair,
    ResourceEstimate,
    SourceMetadata,
    TransientConversionError,
)


class LibreOfficePdfConverter:
    converter_name = "libreoffice-pdf"
    converter_version = "1.0.0"
    progress_mode = "indeterminate"

    formats = ["docx", "odt", "pptx", "xlsx", "html"]

    def supported_pairs(self) -> list[FormatPair]:
        return [FormatPair(source, "pdf") for source in self.formats]

    def option_schema(self) -> ConversionOptionSchema:
        return ConversionOptionSchema(version="1")

    def estimate_cost(self, metadata: SourceMetadata, options: dict) -> ResourceEstimate:
        return ResourceEstimate(seconds=120, memory_mb=1024)

    def probe(self, input_path: Path) -> SourceMetadata:
        return SourceMetadata(
            format=input_path.suffix.lower().lstrip("."), byte_size=input_path.stat().st_size
        )

    def validate_input(self, input_path: Path, metadata: SourceMetadata) -> None:
        if input_path.stat().st_size <= 0:
            raise ValueError("Document is empty")

    def convert(
        self,
        input_path: Path,
        output_path: Path,
        target_format: str,
        options: dict,
        progress_callback=None,
    ):
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            raise TransientConversionError("LibreOffice is not installed in this runtime")
        if progress_callback:
            progress_callback(15, "Starting LibreOffice")
        profile_dir = output_path.parent / "lo-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        command = [
            soffice,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile_dir.as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_path.parent),
            str(input_path),
        ]
        try:
            subprocess.run(command, check=True, timeout=120, capture_output=True)
        except subprocess.CalledProcessError as exc:
            raise TransientConversionError(
                f"LibreOffice failed (exit {exc.returncode})"
            ) from exc
        produced = output_path.parent / f"{input_path.stem}.pdf"
        if produced != output_path and produced.exists():
            produced.replace(output_path)
        return ConversionResult("pdf", output_path.stat().st_size, {"engine": self.converter_name})

    def validate_output(self, output_path: Path, result: ConversionResult) -> None:
        if output_path.stat().st_size <= 4 or not output_path.read_bytes().startswith(b"%PDF"):
            raise ValueError("LibreOffice did not produce a valid PDF")

    def cleanup(self, work_dir: Path) -> None:
        shutil.rmtree(work_dir, ignore_errors=True)
