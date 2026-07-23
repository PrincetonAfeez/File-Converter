# "FFmpeg media converter adapter."
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from .interface import (
    ConversionOptionSchema,
    ConversionResult,
    FormatPair,
    OptionField,
    ResourceEstimate,
    SourceMetadata,
    TransientConversionError,
)

FFMPEG_TIMEOUT_SECONDS = 600
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
_DURATION_RE = re.compile(r"Duration: (\d+):(\d+):(\d+\.\d+)")


def _hms_to_seconds(hours: str, minutes: str, seconds: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


_TRANSIENT_FFMPEG_MARKERS = (
    "Resource temporarily unavailable",
    "Device or resource busy",
    "Cannot allocate memory",
    "Error while opening encoder",
)


class FfmpegMediaConverter:
    converter_name = "ffmpeg-media"
    converter_version = "1.0.0"
    progress_mode = "determinate"

    pairs = [
        FormatPair("wav", "mp3"),
        FormatPair("mp3", "wav"),
        FormatPair("mp4", "mp3"),
        FormatPair("mp4", "gif"),
        FormatPair("mov", "mp4"),
        FormatPair("avi", "mp4"),
    ]

    def supported_pairs(self) -> list[FormatPair]:
        return self.pairs

    def option_schema(self) -> ConversionOptionSchema:
        return ConversionOptionSchema(
            version="1",
            fields=[
                OptionField(
                    "audio_bitrate",
                    "choice",
                    "Audio bitrate",
                    default="192k",
                    choices=["128k", "192k", "256k"],
                )
            ],
        )

    def estimate_cost(self, metadata: SourceMetadata, options: dict) -> ResourceEstimate:
        duration = metadata.duration_seconds or 60
        return ResourceEstimate(seconds=int(duration * 1.5), memory_mb=512)

    def probe(self, input_path: Path) -> SourceMetadata:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return SourceMetadata(
                format=input_path.suffix.lower().lstrip("."), byte_size=input_path.stat().st_size
            )
        command = [
            ffprobe,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(input_path),
        ]
        result = subprocess.run(
            command,
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        )
        payload = json.loads(result.stdout)
        duration = float(payload.get("format", {}).get("duration") or 0)
        return SourceMetadata(
            format=input_path.suffix.lower().lstrip("."),
            byte_size=input_path.stat().st_size,
            duration_seconds=duration,
        )

    def validate_input(self, input_path: Path, metadata: SourceMetadata) -> None:
        if input_path.stat().st_size <= 0:
            raise ValueError("Media input is empty")

    def convert(
        self,
        input_path: Path,
        output_path: Path,
        target_format: str,
        options: dict,
        progress_callback=None,
    ):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise TransientConversionError("FFmpeg is not installed in this runtime")
        command = [ffmpeg, "-y", "-i", str(input_path)]
        if target_format in {"mp3", "wav"}:
            command.append("-vn")
        if target_format == "gif":
            command += ["-vf", "fps=12,scale=720:-1:flags=lanczos"]
        if target_format == "mp3":
            command += ["-b:a", options.get("audio_bitrate", "192k")]
        command += [str(output_path)]

        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + FFMPEG_TIMEOUT_SECONDS
        # Duration is read from ffmpeg's own stderr banner, so no second ffprobe is needed.
        duration = 0.0
        tail: list[str] = []
        try:
            # Stream stderr line-by-line so determinate progress updates live during encoding
            # instead of arriving all at once after the process exits.
            for line in process.stderr:
                tail.append(line)
                del tail[:-40]
                if time.monotonic() > deadline:
                    raise subprocess.TimeoutExpired(command, FFMPEG_TIMEOUT_SECONDS)
                if not duration:
                    dmatch = _DURATION_RE.search(line)
                    if dmatch:
                        duration = _hms_to_seconds(*dmatch.groups())
                if progress_callback and duration:
                    match = _TIME_RE.search(line)
                    if match:
                        elapsed = _hms_to_seconds(*match.groups())
                        progress_callback(
                            min(int((elapsed / duration) * 100), 99), "Encoding media"
                        )
            # Floor the grace period so a loop that exits exactly at the deadline does not
            # spuriously time out (and kill) an already-finished process.
            returncode = process.wait(timeout=max(1.0, deadline - time.monotonic()))
        except BaseException:
            process.kill()
            process.wait()
            raise
        if returncode:
            detail = "".join(tail).strip()[-500:]
            if any(marker in detail for marker in _TRANSIENT_FFMPEG_MARKERS):
                raise TransientConversionError(f"FFmpeg conversion failed: {detail}")
            raise RuntimeError(f"FFmpeg conversion failed: {detail}")
        return ConversionResult(
            target_format, output_path.stat().st_size, {"engine": self.converter_name}
        )

    def validate_output(self, output_path: Path, result: ConversionResult) -> None:
        if output_path.stat().st_size <= 0:
            raise ValueError("Media output is empty")

    def cleanup(self, work_dir: Path) -> None:
        shutil.rmtree(work_dir, ignore_errors=True)
