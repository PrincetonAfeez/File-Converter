# "Pillow image format converter."
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .interface import (
    ConversionOptionSchema,
    ConversionResult,
    FormatPair,
    OptionField,
    ResourceEstimate,
    SourceMetadata,
)


class PillowImageConverter:
    converter_name = "pillow-image"
    converter_version = "1.0.0"
    progress_mode = "indeterminate"

    formats = ["png", "jpg", "jpeg", "webp", "bmp", "tiff"]

    def supported_pairs(self) -> list[FormatPair]:
        return [
            FormatPair(source, target)
            for source in self.formats
            for target in self.formats
            if source != target
        ]

    def option_schema(self) -> ConversionOptionSchema:
        return ConversionOptionSchema(
            version="1",
            fields=[
                OptionField("quality", "integer", "Quality", default=88, minimum=1, maximum=100),
                OptionField("strip_metadata", "boolean", "Strip metadata", default=True),
            ],
        )

    def estimate_cost(self, metadata: SourceMetadata, options: dict) -> ResourceEstimate:
        pixels = (metadata.width or 1000) * (metadata.height or 1000)
        return ResourceEstimate(seconds=max(5, pixels // 2_000_000), memory_mb=256)

    def probe(self, input_path: Path) -> SourceMetadata:
        with Image.open(input_path) as image:
            return SourceMetadata(
                format=(image.format or input_path.suffix.lstrip("."))
                .lower()
                .replace("jpeg", "jpg"),
                mime_type=Image.MIME.get(image.format, ""),
                byte_size=input_path.stat().st_size,
                width=image.width,
                height=image.height,
            )

    def validate_input(self, input_path: Path, metadata: SourceMetadata) -> None:
        try:
            with Image.open(input_path) as image:
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("Uploaded image cannot be opened safely") from exc

    def convert(
        self,
        input_path: Path,
        output_path: Path,
        target_format: str,
        options: dict,
        progress_callback=None,
    ):
        if progress_callback:
            progress_callback(10, "Opening image")
        target_format = target_format.lower()
        pillow_format = "JPEG" if target_format in {"jpg", "jpeg"} else target_format.upper()
        with Image.open(input_path) as image:
            if target_format in {"jpg", "jpeg"} and image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            save_kwargs = {}
            if target_format in {"jpg", "jpeg", "webp"}:
                save_kwargs["quality"] = int(options.get("quality", 88))
                save_kwargs["optimize"] = True
            if options.get("strip_metadata", True):
                # Rebuild the image without EXIF/ICC/other metadata chunks while preserving
                # pixel data AND the palette for mode "P" images (a plain new+putdata would
                # drop the palette and corrupt the colors).
                stripped = Image.new(image.mode, image.size)
                stripped.putdata(list(image.getdata()))
                if image.mode in {"P", "PA"}:
                    palette = image.getpalette()
                    if palette:
                        stripped.putpalette(palette)
                image = stripped
            if progress_callback:
                progress_callback(75, "Writing output")
            image.save(output_path, pillow_format, **save_kwargs)
        return ConversionResult(
            output_format=target_format,
            byte_size=output_path.stat().st_size,
            metadata={"engine": self.converter_name},
        )

    def validate_output(self, output_path: Path, result: ConversionResult) -> None:
        if output_path.stat().st_size <= 0:
            raise ValueError("Converted image is empty")
        with Image.open(output_path) as image:
            image.verify()

    def cleanup(self, work_dir: Path) -> None:
        shutil.rmtree(work_dir, ignore_errors=True)
