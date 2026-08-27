from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable, Iterable
from zipfile import BadZipFile, ZipFile


ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


@dataclass(frozen=True, slots=True)
class ZipEntryPayload:
    entry_name: str
    value: Any
    entries: tuple[str, ...]

    def provenance(self) -> dict[str, Any]:
        files = [name for name in self.entries if not name.endswith("/")]
        return {
            "type": "zip",
            "json_entry": self.entry_name,
            "entries": list(self.entries),
            "other_entries": [name for name in self.entries if name != self.entry_name],
            # These are archive file entries, not interpreted file contents.
            # Keeping the complete index lets a later attachment importer match
            # source references without this parser guessing archive semantics.
            "attachment_entries": [name for name in files if name != self.entry_name],
        }


@dataclass(frozen=True, slots=True)
class ZipJsonCollection:
    entry_names: tuple[str, ...]
    values: tuple[Any, ...]
    entries: tuple[str, ...]

    def provenance(self) -> dict[str, Any]:
        selected = set(self.entry_names)
        files = [name for name in self.entries if not name.endswith("/")]
        metadata: dict[str, Any] = {
            "type": "zip",
            "json_entries": list(self.entry_names),
            "entries": list(self.entries),
            "other_entries": [name for name in self.entries if name not in selected],
            "attachment_entries": [name for name in files if name not in selected],
        }
        if len(self.entry_names) == 1:
            metadata["json_entry"] = self.entry_names[0]
        return metadata


def is_zip_payload(payload: Any) -> bool:
    return isinstance(payload, bytes) and payload.startswith(ZIP_SIGNATURES)


def read_zip_json_entry(
    payload: bytes,
    entry_names: Iterable[str],
) -> ZipEntryPayload | None:
    """Read the first exact, root-relative JSON entry from a ZIP archive."""

    with ZipFile(BytesIO(payload)) as archive:
        entries = tuple(archive.namelist())
        available = set(entries)
        for entry_name in entry_names:
            if entry_name not in available:
                continue
            raw = archive.read(entry_name)
            value = json.loads(raw.decode("utf-8-sig"))
            return ZipEntryPayload(entry_name, value, entries)
    return None


def read_zip_json_entries_matching(
    payload: bytes,
    matches: Callable[[str], bool],
) -> ZipJsonCollection:
    """Decode exact archive members selected by a format-specific filename rule."""

    with ZipFile(BytesIO(payload)) as archive:
        entries = tuple(archive.namelist())
        selected = tuple(name for name in entries if matches(name))
        values = tuple(
            json.loads(archive.read(name).decode("utf-8-sig"))
            for name in selected
        )
    return ZipJsonCollection(selected, values, entries)


def read_zip_entries(payload: bytes, entry_names: Iterable[str]) -> tuple[tuple[str, ...], dict[str, bytes]]:
    """Read a fixed set of exact archive paths without extracting the ZIP."""

    with ZipFile(BytesIO(payload)) as archive:
        entries = tuple(archive.namelist())
        available = set(entries)
        return entries, {
            entry_name: archive.read(entry_name)
            for entry_name in entry_names
            if entry_name in available
        }


__all__ = [
    "BadZipFile",
    "ZipEntryPayload",
    "ZipJsonCollection",
    "is_zip_payload",
    "read_zip_entries",
    "read_zip_json_entry",
    "read_zip_json_entries_matching",
]
