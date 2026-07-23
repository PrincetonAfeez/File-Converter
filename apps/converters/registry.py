# "Format-pair to converter registry."
from __future__ import annotations

from dataclasses import dataclass

from .interface import Converter


class UnsupportedFormatPair(ValueError):
    pass


@dataclass(frozen=True)
class RegisteredConverter:
    source: str
    target: str
    converter: Converter


class ConverterRegistry:
    def __init__(self) -> None:
        self._loaded = False
        self._pairs: dict[tuple[str, str], Converter] = {}

    def register(self, converter: Converter) -> None:
        for pair in converter.supported_pairs():
            key = (pair.source.lower(), pair.target.lower())
            if key in self._pairs:
                raise ValueError(f"Duplicate converter registration for {key}")
            self._pairs[key] = converter

    def load_builtin_converters(self) -> None:
        if self._loaded:
            return
        from .data import DataTableConverter
        from .documents import LibreOfficePdfConverter
        from .images import PillowImageConverter
        from .media import FfmpegMediaConverter

        self.register(PillowImageConverter())
        self.register(DataTableConverter())
        self.register(LibreOfficePdfConverter())
        self.register(FfmpegMediaConverter())
        self._loaded = True

    def get(self, source: str, target: str) -> Converter:
        self.load_builtin_converters()
        try:
            return self._pairs[(source.lower(), target.lower())]
        except KeyError:
            raise UnsupportedFormatPair(f"{source} to {target} is not supported")

    def targets_for_source(self, source: str) -> list[str]:
        self.load_builtin_converters()
        return sorted(
            target for (src, target), _converter in self._pairs.items() if src == source.lower()
        )

    def all_pairs(self) -> list[RegisteredConverter]:
        self.load_builtin_converters()
        return [
            RegisteredConverter(source=source, target=target, converter=converter)
            for (source, target), converter in sorted(self._pairs.items())
        ]


registry = ConverterRegistry()
