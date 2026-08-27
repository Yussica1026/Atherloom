from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from backend.importers.api_models import (
    ImportCommitIn,
    ImportEncodedPreviewIn,
    ImportPreviewIn,
)
from backend.importers.registry import ImporterError, ImporterRegistry
from backend.importers.repository import (
    ImportBatchConflict,
    ImportBatchDataError,
    ImportBatchNotFound,
    ImportBatchRepository,
)
from backend.importers.schema import DbPath
from backend.importers.service import ConversationImportService


def create_router(
    db_path: DbPath,
    registry: ImporterRegistry,
) -> tuple[APIRouter, Any]:
    router = APIRouter(tags=["conversation-imports"])
    repository = ImportBatchRepository(db_path)
    service = ConversationImportService(repository, registry)

    def format_options() -> list[dict[str, Any]]:
        describe = getattr(registry, "list_format_details", None)
        if callable(describe):
            return [
                {
                    "id": detail.format_id,
                    "platform": detail.platform,
                    "aliases": list(detail.aliases),
                    "file_extensions": list(detail.file_extensions),
                    "media_types": list(detail.media_types),
                }
                for detail in describe()
            ]
        return [
            {
                "id": format_id,
                "platform": format_id.partition(".")[0],
                "aliases": [],
                "file_extensions": [".json"],
                "media_types": ["application/json"],
            }
            for format_id in registry.list_formats()
        ]

    def file_capabilities() -> tuple[set[str], set[str]]:
        options = format_options()
        extensions = {
            str(extension).lower()
            for option in options
            for extension in option["file_extensions"]
        }
        media_types = {
            str(media_type).lower()
            for option in options
            for media_type in option["media_types"]
        }
        media_types.add("application/octet-stream")
        return extensions, media_types

    def validate_file_payload(source_name: str, payload: bytes) -> None:
        extensions, _ = file_capabilities()
        extension = Path(source_name).suffix.lower()
        if extension not in extensions:
            raise HTTPException(
                415,
                f"不支持的导入文件扩展名：{extension or '(无)'}",
            )
        if not payload:
            raise HTTPException(400, "导入文件为空")

    def invoke(method: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except ImportBatchNotFound as error:
            raise HTTPException(404, str(error)) from error
        except (ImporterError, ImportBatchConflict) as error:
            status = 400 if isinstance(error, ImporterError) else 409
            raise HTTPException(status, str(error)) from error
        except ImportBatchDataError as error:
            raise HTTPException(500, str(error)) from error

    @router.get("/api/imports/formats")
    def list_formats() -> dict[str, Any]:
        options = format_options()
        extensions, media_types = file_capabilities()
        return {
            "formats": list(registry.list_formats()),
            "format_options": options,
            "upload": {
                "endpoint": "/api/imports/preview-file",
                "method": "POST",
                "body": "raw-file",
                "source_name_parameter": "source_name",
                "format_hint_parameter": "format_hint",
                "accepted_extensions": sorted(extensions),
                "accepted_media_types": sorted(media_types),
                "auto_detect": True,
            },
            "encoded_upload": {
                "endpoint": "/api/imports/preview-encoded",
                "method": "POST",
                "body": "json-base64",
                "fields": ["source_name", "content_base64", "format_hint"],
                "accepted_extensions": sorted(extensions),
                "auto_detect": True,
            },
        }

    @router.post("/api/imports/preview", status_code=201)
    def preview(body: ImportPreviewIn) -> dict[str, Any]:
        return invoke(
            service.preview,
            body.payload,
            source_name=body.source_name,
            format_hint=body.format_hint,
        )

    @router.post("/api/imports/preview-file", status_code=201)
    async def preview_file(
        request: Request,
        source_name: str = Query(min_length=1, max_length=500),
        format_hint: str | None = Query(default=None, min_length=1, max_length=100),
    ) -> dict[str, Any]:
        _, media_types = file_capabilities()
        content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        content_type = content_type or "application/octet-stream"
        if content_type not in media_types:
            raise HTTPException(415, f"不支持的导入媒体类型：{content_type}")
        payload = await request.body()
        validate_file_payload(source_name, payload)
        return invoke(
            service.preview,
            payload,
            source_name=source_name,
            format_hint=format_hint,
        )

    @router.post("/api/imports/preview-encoded", status_code=201)
    def preview_encoded(body: ImportEncodedPreviewIn) -> dict[str, Any]:
        try:
            payload = base64.b64decode(body.content_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise HTTPException(400, "content_base64 不是严格有效的 base64") from error
        validate_file_payload(body.source_name, payload)
        return invoke(
            service.preview,
            payload,
            source_name=body.source_name,
            format_hint=body.format_hint,
        )

    @router.get("/api/imports/batches")
    def list_batches(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        return {"batches": invoke(service.list_batches, limit)}

    @router.get("/api/imports/batches/{batch_id}")
    def get_batch(batch_id: str) -> dict[str, Any]:
        return invoke(service.get_batch, batch_id)

    @router.post("/api/imports/batches/{batch_id}/commit")
    def commit(
        batch_id: str,
        body: ImportCommitIn | None = None,
    ) -> dict[str, Any]:
        return invoke(
            service.commit,
            batch_id,
            selected_conversation_ids=(
                body.selected_conversation_ids if body is not None else None
            ),
            persona_mapping=body.persona_mapping if body is not None else None,
        )

    @router.post("/api/imports/batches/{batch_id}/rollback")
    def rollback(batch_id: str) -> dict[str, Any]:
        return invoke(service.rollback, batch_id)

    @router.get("/api/imports/conversations/{conversation_id}/provenance")
    def provenance(conversation_id: str) -> dict[str, Any]:
        return invoke(service.conversation_provenance, conversation_id)

    return router, repository.init_schema
