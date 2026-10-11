from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .archive import is_zip_payload
from .models import CanonicalImportBatch, SourcePlatform


class ImporterError(ValueError):
    pass


class UnsupportedExportFormat(ImporterError):
    pass


class AmbiguousExportFormat(ImporterError):
    pass


@dataclass(frozen=True, slots=True)
class DetectionResult:
    format_id: str
    platform: SourcePlatform
    confidence: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ImporterFormatDetails:
    format_id: str
    platform: SourcePlatform
    aliases: tuple[str, ...]
    file_extensions: tuple[str, ...]
    media_types: tuple[str, ...]


class ConversationImporter(Protocol):
    format_id: str
    platform: SourcePlatform
    aliases: tuple[str, ...]
    file_extensions: tuple[str, ...]
    media_types: tuple[str, ...]

    def detect(self, payload: Any, source_name: str | None = None) -> DetectionResult | None: ...

    def parse(self, payload: Any, source_name: str | None = None) -> CanonicalImportBatch: ...


def decode_json_payload(payload: Any) -> Any:
    if isinstance(payload, bytes):
        # Container-aware importers inspect ZIP bytes themselves.  Do not try
        # to decode a binary archive as UTF-8 before format detection runs.
        if is_zip_payload(payload):
            return payload
        try:
            payload = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ImporterError("Export is not valid UTF-8 JSON") from exc
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            # JSONL importers need the original text.  Detection remains
            # conservative, so arbitrary malformed JSON is still rejected as
            # unsupported when no registered text importer recognizes it.
            return payload
    return payload


class ImporterRegistry:
    def __init__(self) -> None:
        self._importers: dict[str, ConversationImporter] = {}
        self._aliases: dict[str, str] = {}

    def register(self, importer: ConversationImporter) -> None:
        if importer.format_id in self._importers:
            raise ValueError(f"Importer already registered: {importer.format_id}")
        self._importers[importer.format_id] = importer
        for alias in (importer.format_id, importer.platform, *importer.aliases):
            normalized = alias.strip().lower()
            if normalized in self._aliases and self._aliases[normalized] != importer.format_id:
                raise ValueError(f"Importer alias already registered: {alias}")
            self._aliases[normalized] = importer.format_id

    def list_formats(self) -> tuple[str, ...]:
        return tuple(self._importers)

    def list_format_details(self) -> tuple[ImporterFormatDetails, ...]:
        return tuple(
            ImporterFormatDetails(
                format_id=importer.format_id,
                platform=importer.platform,
                aliases=importer.aliases,
                file_extensions=importer.file_extensions,
                media_types=importer.media_types,
            )
            for importer in self._importers.values()
        )

    def detector_results(self, payload: Any, source_name: str | None = None) -> list[DetectionResult]:
        decoded = decode_json_payload(payload)
        results = [
            result
            for importer in self._importers.values()
            if (result := importer.detect(decoded, source_name)) is not None
        ]
        return sorted(results, key=lambda result: (-result.confidence, result.format_id))

    def detect(self, payload: Any, source_name: str | None = None) -> DetectionResult:
        results = self.detector_results(payload, source_name)
        if not results or results[0].confidence < 0.5:
            raise UnsupportedExportFormat("No registered importer recognizes this export")
        if len(results) > 1 and results[0].confidence == results[1].confidence:
            raise AmbiguousExportFormat(
                f"Export matches both {results[0].format_id} and {results[1].format_id}"
            )
        return results[0]

    def parse(
        self,
        payload: Any,
        *,
        source_name: str | None = None,
        format_hint: str | None = None,
    ) -> CanonicalImportBatch:
        decoded = decode_json_payload(payload)
        if format_hint:
            format_id = self._aliases.get(format_hint.strip().lower())
            if not format_id:
                raise UnsupportedExportFormat(f"Unknown import format hint: {format_hint}")
        else:
            format_id = self.detect(decoded, source_name).format_id
        return self._importers[format_id].parse(decoded, source_name)
