from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database.repository import SearchFilters, VideoRepository
from drive_scanner.runner import run_scan
from utils.config import Settings
from utils.formatters import format_bytes, format_duration

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def create_app(settings: Settings) -> FastAPI:
    settings.ensure_dirs()
    repo = VideoRepository(settings.db_path)

    app = FastAPI(title="Google Drive Video Library", version="1.0.0")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "app_name": settings.app_name,
            },
        )

    @app.get("/api/stats")
    async def stats() -> dict[str, Any]:
        data = repo.get_stats()
        return {
            **data,
            "total_size_human": format_bytes(data["total_bytes"]),
            "total_duration_human": format_duration(data["total_duration_seconds"]),
        }

    @app.get("/api/filters")
    async def filters() -> dict[str, list[str]]:
        return repo.get_filter_options()

    @app.post("/api/scan-folder")
    async def scan_folder(payload: dict[str, Any]) -> JSONResponse:
        raw = (payload.get("folder_url_or_id") or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="folder_url_or_id is required")

        folder_id = _extract_folder_id(raw)
        if not folder_id:
            raise HTTPException(status_code=400, detail="Could not extract folder ID from input")

        scan_result = run_scan(settings, full=False, folder_id=folder_id)
        return JSONResponse(
            {
                "folder_id": folder_id,
                "videos_found": scan_result.videos_found,
                "videos_indexed": scan_result.videos_indexed,
                "videos_skipped": scan_result.videos_skipped,
                "errors": scan_result.errors,
            }
        )

    @app.get("/api/videos")
    async def videos(
        q: str = Query(default=""),
        folder: str = Query(default=""),
        extension: str = Query(default=""),
        resolution: str = Query(default=""),
        year: str = Query(default=""),
        shared_drive: str = Query(default=""),
        semantic: bool = Query(default=False, description="Use FTS semantic search"),
        min_size_mb: float | None = Query(default=None),
        max_size_mb: float | None = Query(default=None),
        min_duration_sec: float | None = Query(default=None),
        max_duration_sec: float | None = Query(default=None),
        has_audio: bool | None = Query(default=None),
        sort_by: str = Query(default="file_name"),
        sort_dir: str = Query(default="asc"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        if sort_by not in {
            "file_name",
            "folder_path",
            "file_size",
            "duration_seconds",
            "modified_at",
            "resolution",
            "file_extension",
            "internal_video_id",
            "speaker",
            "main_theme",
        }:
            raise HTTPException(status_code=400, detail="Invalid sort column")

        result = repo.search(
            SearchFilters(
                query=q,
                folder=folder,
                extension=extension,
                resolution=resolution,
                year=year,
                shared_drive=shared_drive,
                min_size_mb=min_size_mb,
                max_size_mb=max_size_mb,
                min_duration_sec=min_duration_sec,
                max_duration_sec=max_duration_sec,
                has_audio=has_audio,
            ),
            sort_by=sort_by,
            sort_dir=sort_dir,
            page=page,
            page_size=page_size,
            use_fts=semantic,
        )

        return {
            "total": result.total,
            "page": result.page,
            "page_size": result.page_size,
            "total_pages": result.total_pages,
            "items": [
                {
                    **item.to_dict(),
                    "file_size_human": format_bytes(item.file_size),
                    "duration_human": format_duration(item.duration_seconds),
                }
                for item in result.items
            ],
        }

    @app.get("/api/videos/{file_id}")
    async def video_detail(file_id: str) -> dict[str, Any]:
        item = repo.get_video(file_id)
        if not item:
            raise HTTPException(status_code=404, detail="Video not found")
        payload = item.to_dict()
        payload["file_size_human"] = format_bytes(item.file_size)
        payload["duration_human"] = format_duration(item.duration_seconds)
        return payload

    return app


def _extract_folder_id(raw: str) -> str | None:
    # Supports plain ID or URLs like
    # https://drive.google.com/drive/folders/{ID}
    # https://drive.google.com/drive/u/0/folders/{ID}
    # https://drive.google.com/open?id={ID}
    if "drive.google.com" not in raw and "http" not in raw:
        return raw

    # folders URL
    if "/folders/" in raw:
        after = raw.split("/folders/", 1)[1]
        return after.split("?", 1)[0].split("/", 1)[0]

    if "id=" in raw:
        after = raw.split("id=", 1)[1]
        return after.split("&", 1)[0]

    return None
