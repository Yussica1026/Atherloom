from __future__ import annotations

from typing import Any

from .astrbot import AstrBotConversationsImporter
from .chatgpt import ChatGPTConversationsImporter
from .claude import ClaudeConversationsImporter
from .kelivo import (
    KelivoBackupImporter,
    KelivoSQLiteBackupUnsupported,
    KelivoZipPayloadUnsupported,
)
from .models import (
    CanonicalBranch,
    CanonicalContentBlock,
    CanonicalConversation,
    CanonicalImportBatch,
    CanonicalMessage,
    CanonicalMessageNode,
    CanonicalParticipant,
    ImportWarning,
    SourceProvenance,
)
from .registry import (
    AmbiguousExportFormat,
    DetectionResult,
    ImporterFormatDetails,
    ImporterError,
    ImporterRegistry,
    UnsupportedExportFormat,
)


DEFAULT_IMPORTER_REGISTRY = ImporterRegistry()
DEFAULT_IMPORTER_REGISTRY.register(ChatGPTConversationsImporter())
DEFAULT_IMPORTER_REGISTRY.register(ClaudeConversationsImporter())
DEFAULT_IMPORTER_REGISTRY.register(AstrBotConversationsImporter())
DEFAULT_IMPORTER_REGISTRY.register(KelivoBackupImporter())


def detect_export(payload: Any, source_name: str | None = None) -> DetectionResult:
    return DEFAULT_IMPORTER_REGISTRY.detect(payload, source_name)


def parse_export(
    payload: Any,
    *,
    source_name: str | None = None,
    format_hint: str | None = None,
) -> CanonicalImportBatch:
    """Parse an official export into canonical objects without persistence side effects."""

    return DEFAULT_IMPORTER_REGISTRY.parse(
        payload,
        source_name=source_name,
        format_hint=format_hint,
    )


__all__ = [
    "AmbiguousExportFormat",
    "CanonicalBranch",
    "CanonicalContentBlock",
    "CanonicalConversation",
    "CanonicalImportBatch",
    "CanonicalMessage",
    "CanonicalMessageNode",
    "CanonicalParticipant",
    "DEFAULT_IMPORTER_REGISTRY",
    "DetectionResult",
    "ImporterError",
    "ImporterFormatDetails",
    "ImporterRegistry",
    "ImportWarning",
    "KelivoSQLiteBackupUnsupported",
    "KelivoZipPayloadUnsupported",
    "SourceProvenance",
    "UnsupportedExportFormat",
    "detect_export",
    "parse_export",
]
