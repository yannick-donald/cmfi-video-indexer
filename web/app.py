from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from auth.email_sender import EmailDeliveryError, EmailSender
from database.auth import AuthError, AuthRepository, LoginThrottledError
from database.repository import SearchFilters, VideoRepository
from demo_data import seed_public_demo
from auth.drive_auth import authenticate
from drive_scanner.client import build_drive_service
from drive_scanner.scanner import FOLDER_MIME
from drive_scanner.runner import run_scan
from reporting.excel_exporter import export_to_stream
from utils.config import Settings
from utils.formatters import format_bytes, format_duration

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
LOGGER = logging.getLogger(__name__)


def create_app(settings: Settings) -> FastAPI:
    settings.ensure_dirs()
    repo = VideoRepository(settings.db_path, settings.database_url)
    auth_repo = AuthRepository(settings.db_path, settings.database_url)
    email_sender = EmailSender(settings)
    if settings.purge_demo_data:
        deleted_demo_count = repo.delete_demo_videos()
        if deleted_demo_count:
            LOGGER.info("Removed %s demo videos", deleted_demo_count)
    if settings.auto_seed_demo and repo.get_stats()["total_videos"] == 0:
        seed_public_demo(repo)
    if settings.admin_email and settings.admin_password:
        auth_repo.ensure_user(settings.admin_email, settings.admin_password)

    app = FastAPI(title="Google Drive Video Library", version="1.0.0")
    scan_lock = asyncio.Lock()
    background_tasks: set[asyncio.Task[Any]] = set()
    scan_state: dict[str, Any] = _initial_scan_state(repo)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def require_authentication(request: Request, call_next: Any) -> Any:
        public_path = (
            request.url.path == "/health"
            or request.url.path == "/login"
            or request.url.path == "/favicon.ico"
            or request.url.path.startswith("/api/auth/")
            or request.url.path.startswith("/static/")
        )
        token = request.cookies.get(settings.session_cookie_name, "")
        user = auth_repo.get_session_user(token) if token else None
        if user:
            profile = auth_repo.get_user(int(user["id"]))
            user["full_name"] = (profile or {}).get("full_name", "")
            user["display_name"] = (profile or {}).get("display_name", user["email"])
            user["is_super_admin"] = _is_super_admin(settings, str(user["email"]))
            user["can_scan_drive"] = bool(_configured_scan_folder_id(repo, settings))
        request.state.user = user
        if settings.auth_required and not public_path and not user:
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> HTMLResponse:
        if request.state.user:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request,
            "auth.html",
            {
                "request": request,
                "app_name": settings.app_name,
                "allow_registration": settings.allow_registration,
            },
        )

    @app.post("/api/auth/register")
    async def register(payload: dict[str, Any]) -> JSONResponse:
        if not settings.allow_registration:
            raise HTTPException(status_code=403, detail="Registration is disabled")
        try:
            user = auth_repo.create_user(
                str(payload.get("email") or ""),
                str(payload.get("password") or ""),
                email_verified=not settings.email_verification_required,
                full_name=str(payload.get("full_name") or ""),
            )
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if settings.email_verification_required:
            try:
                _send_verification_code(auth_repo, email_sender, settings, str(user["email"]))
            except (AuthError, EmailDeliveryError) as exc:
                auth_repo.delete_unverified_user(int(user["id"]))
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return JSONResponse(
                {
                    "ok": True,
                    "verification_required": True,
                    "email": user["email"],
                }
            )
        return _session_response(auth_repo, settings, user)

    @app.post("/api/auth/login")
    async def login(request: Request, payload: dict[str, Any]) -> JSONResponse:
        email = str(payload.get("email") or "")
        identifiers = _login_identifiers(request, settings, email)
        try:
            auth_repo.check_login_allowed(identifiers)
        except LoginThrottledError as exc:
            raise HTTPException(
                status_code=429,
                detail=str(exc),
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc

        try:
            user = auth_repo.authenticate(email, str(payload.get("password") or ""))
        except AuthError as exc:
            if "confirmer votre adresse" in str(exc):
                # Correct credentials on an unverified account: not a failed
                # attempt, so it must not count towards the lockout.
                auth_repo.clear_login_failures(identifiers)
                return JSONResponse(
                    {
                        "detail": str(exc),
                        "verification_required": True,
                        "email": email.strip().casefold(),
                    },
                    status_code=403,
                )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not user:
            auth_repo.register_failed_login(
                identifiers,
                max_attempts=settings.login_max_attempts,
                lockout_minutes=settings.login_lockout_minutes,
            )
            raise HTTPException(status_code=401, detail="E-mail ou mot de passe incorrect")
        auth_repo.clear_login_failures(identifiers)
        return _session_response(auth_repo, settings, user)

    @app.post("/api/auth/verify-email")
    async def verify_email(payload: dict[str, Any]) -> JSONResponse:
        try:
            user = auth_repo.verify_email(
                str(payload.get("email") or ""),
                str(payload.get("code") or ""),
            )
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _session_response(auth_repo, settings, user)

    @app.post("/api/auth/resend-verification")
    async def resend_verification(payload: dict[str, Any]) -> dict[str, Any]:
        if not settings.email_verification_required:
            raise HTTPException(status_code=400, detail="La vérification par e-mail est désactivée")
        try:
            _send_verification_code(
                auth_repo,
                email_sender,
                settings,
                str(payload.get("email") or ""),
                enforce_cooldown=True,
            )
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except EmailDeliveryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        auth_repo.delete_session(request.cookies.get(settings.session_cookie_name, ""))
        response = JSONResponse({"ok": True})
        response.delete_cookie(settings.session_cookie_name)
        return response

    @app.get("/api/auth/me")
    async def current_user(request: Request) -> dict[str, Any]:
        if not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return request.state.user

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "app_name": settings.app_name,
                "public_demo": settings.public_demo,
                "read_only": settings.read_only,
                "auth_required": settings.auth_required,
                "current_user": request.state.user,
                "drive_scan_configured": bool(_configured_scan_folder_id(repo, settings)),
                "drive_folder": _drive_folder_payload(repo, settings),
            },
        )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        """Les navigateurs sollicitent la racine, pas /static, pour l'icône."""
        return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "videos": repo.get_stats()["total_videos"],
            "read_only": settings.read_only,
        }

    @app.get("/api/stats")
    async def stats() -> dict[str, Any]:
        data = repo.get_stats()
        return {
            **data,
            "total_size_human": format_bytes(data["total_bytes"]),
            "total_duration_human": format_duration(data["total_duration_seconds"]),
        }

    @app.get("/api/filters")
    async def filters() -> dict[str, Any]:
        return {
            **repo.get_filter_options(),
            "labels": repo.get_all_labels(),
        }

    @app.get("/api/workflow/stats")
    async def workflow_stats() -> dict[str, Any]:
        return {
            **repo.get_workflow_stats(),
            "tracking": repo.get_tracking_stats(),
            "assignments": repo.get_assignment_stats(),
        }

    @app.get("/api/workflow/raw-videos")
    async def raw_video_options(
        exclude_file_id: str = Query(default=""),
        q: str = Query(default="", max_length=120),
        limit: int = Query(default=20, ge=1, le=50),
    ) -> dict[str, Any]:
        """Ranked source candidates for a cut. An empty q returns the newest."""
        return {"items": repo.get_raw_video_options(exclude_file_id, query=q, limit=limit)}

    @app.get("/api/workflow/relink-queue")
    async def relink_queue(
        limit: int = Query(default=25, ge=1, le=100),
        suggestions: int = Query(default=3, ge=1, le=5),
    ) -> dict[str, Any]:
        """Cut videos with no source yet, each with its best candidate sources."""
        def build() -> list[dict[str, Any]]:
            return [
                {**cut, "suggestions": repo.suggest_sources_for(cut["file_id"], suggestions)}
                for cut in repo.get_unlinked_cuts(limit)
            ]

        items = await asyncio.to_thread(build)
        return {"items": items, "total": len(items)}

    @app.post("/api/videos/{file_id}/link-source")
    async def link_source(request: Request, file_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        _require_writable(settings)
        if settings.auth_required and not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")
        source_file_id = str(payload.get("source_file_id") or "").strip()
        if not source_file_id:
            raise HTTPException(status_code=400, detail="source_file_id is required")
        try:
            video = repo.link_cut_source(file_id, source_file_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        return {"file_id": file_id, "source_file_id": video.source_file_id, "asset_type": video.asset_type}

    @app.get("/api/scan-folder/status")
    async def scan_folder_status(request: Request) -> dict[str, Any]:
        _require_drive_scan(repo, settings, request.state.user)
        return dict(scan_state)

    @app.post("/api/scan-folder")
    async def scan_folder(request: Request) -> JSONResponse:
        folder_id = _require_drive_scan(repo, settings, request.state.user)
        if scan_lock.locked():
            return JSONResponse(
                {"detail": "Un scan Drive est déjà en cours", **scan_state},
                status_code=409,
            )

        await scan_lock.acquire()
        try:
            _update_scan_state(
                repo,
                scan_state,
                {
                    "status": "running",
                    "started_at": _utc_now(),
                    "finished_at": None,
                    "videos_found": 0,
                    "videos_indexed": 0,
                    "videos_skipped": 0,
                    "folders_scanned": 0,
                    "cuts_linked": 0,
                    "errors": 0,
                    "message": "Analyse du dossier Drive en cours",
                },
            )
            task = asyncio.create_task(
                _run_drive_scan(settings, repo, folder_id, scan_lock, scan_state)
            )
        except BaseException:
            # The task owns the release; if it never started, release here or the
            # endpoint would answer 409 forever.
            scan_lock.release()
            raise
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return JSONResponse(dict(scan_state), status_code=202)

    @app.get("/api/admin/users")
    async def list_users(request: Request) -> dict[str, Any]:
        _require_super_admin(settings, request.state.user)
        users = auth_repo.list_users()
        for user in users:
            user["is_super_admin"] = _is_super_admin(settings, str(user["email"]))
        return {"items": users, "assignments": repo.get_assignment_stats()}

    @app.put("/api/admin/users/{user_id}/status")
    async def set_user_status(request: Request, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        admin = _require_super_admin(settings, request.state.user)
        _require_writable(settings)
        target = auth_repo.get_user(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Compte introuvable")
        if _is_super_admin(settings, str(target["email"])):
            raise HTTPException(
                status_code=400,
                detail="Le compte administrateur ne peut pas être désactivé",
            )
        if int(admin["id"]) == user_id:
            raise HTTPException(status_code=400, detail="Vous ne pouvez pas désactiver votre propre compte")
        updated = auth_repo.set_user_active(user_id, bool(payload.get("is_active")))
        if not updated:
            raise HTTPException(status_code=404, detail="Compte introuvable")
        return updated

    @app.put("/api/admin/users/{user_id}/name")
    async def set_user_name(request: Request, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        _require_super_admin(settings, request.state.user)
        _require_writable(settings)
        updated = auth_repo.set_user_name(user_id, str(payload.get("full_name") or ""))
        if not updated:
            raise HTTPException(status_code=404, detail="Compte introuvable")
        return updated

    @app.put("/api/videos/{file_id}/assignee")
    async def assign_video(request: Request, file_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Designate who works on a video. Reserved to the administrator."""
        admin = _require_super_admin(settings, request.state.user)
        _require_writable(settings)
        raw_user_id = payload.get("user_id")
        if raw_user_id in (None, "", 0):
            video = repo.assign_video(file_id, None, "", assigned_by_email=str(admin["email"]))
        else:
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="user_id invalide") from exc
            target = auth_repo.get_user(user_id)
            if not target:
                raise HTTPException(status_code=404, detail="Compte introuvable")
            if not target["is_active"]:
                raise HTTPException(
                    status_code=400,
                    detail="Ce compte est désactivé : réactivez-le avant de lui affecter une vidéo",
                )
            video = repo.assign_video(
                file_id, user_id, str(target["email"]), assigned_by_email=str(admin["email"])
            )
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        return {
            "file_id": file_id,
            "assigned_user_id": video.assigned_user_id,
            "assigned_user_email": video.assigned_user_email,
            "assigned_at": video.assigned_at,
            "assigned_by_email": video.assigned_by_email,
            "assigned_user_name": repo.get_user_display_names().get(
                video.assigned_user_email, video.assigned_user_email
            ),
        }

    @app.get("/api/admin/drive-folder")
    async def drive_folder_config(request: Request) -> dict[str, Any]:
        _require_super_admin(settings, request.state.user)
        return _drive_folder_payload(repo, settings)

    @app.get("/api/admin/drive-folders/search")
    async def search_drive_folders(
        request: Request,
        q: str = Query(default="", min_length=1, max_length=120),
    ) -> dict[str, Any]:
        _require_super_admin(settings, request.state.user)
        service = _drive_service_or_http_error(settings)
        safe_query = q.strip().replace("\\", "\\\\").replace("'", "\\'")
        try:
            response = await asyncio.to_thread(
                lambda: service.files()
                .list(
                    q=(
                        f"mimeType = '{FOLDER_MIME}' and trashed = false "
                        f"and name contains '{safe_query}'"
                    ),
                    pageSize=30,
                    fields="files(id,name,webViewLink,driveId)",
                    corpora="allDrives",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                )
                .execute()
            )
        except Exception as exc:
            LOGGER.exception("Drive folder search failed for query=%s", q)
            raise HTTPException(
                status_code=502,
                detail="La recherche Drive a échoué. Vérifiez le partage et les identifiants Google.",
            ) from exc
        return {
            "items": [
                {
                    "folder_id": item["id"],
                    "folder_name": item.get("name", item["id"]),
                    "folder_url": item.get("webViewLink")
                    or f"https://drive.google.com/drive/folders/{item['id']}",
                    "shared_drive": bool(item.get("driveId")),
                }
                for item in response.get("files", [])
            ]
        }

    @app.post("/api/admin/drive-folder/test")
    async def test_drive_folder(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        _require_super_admin(settings, request.state.user)
        folder_id = _extract_folder_id(str(payload.get("folder_url_or_id") or "").strip())
        if not folder_id:
            raise HTTPException(status_code=400, detail="Lien ou identifiant Drive invalide")
        return await _resolve_drive_folder(settings, folder_id)

    @app.put("/api/admin/drive-folder")
    async def update_drive_folder(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        user = _require_super_admin(settings, request.state.user)
        folder_id = _extract_folder_id(str(payload.get("folder_url_or_id") or "").strip())
        if not folder_id:
            raise HTTPException(status_code=400, detail="Lien ou identifiant Drive invalide")
        folder = await _resolve_drive_folder(settings, folder_id)
        return repo.set_drive_folder_setting(
            folder_id=folder["folder_id"],
            folder_name=folder["folder_name"],
            folder_url=folder["folder_url"],
            user_email=str(user["email"]),
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
        asset_type: str = Query(default=""),
        workflow_stage: str = Query(default=""),
        label: str = Query(default=""),
        tracking: str = Query(default=""),
        assignee: str = Query(default=""),
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
            "editorial_title",
            "speaker",
            "preacher",
            "main_theme",
            "content_type",
            "event_name",
            "asset_type",
            "workflow_stage",
        }:
            raise HTTPException(status_code=400, detail="Invalid sort column")

        result = repo.search(
            _search_filters(
                q=q, folder=folder, extension=extension, resolution=resolution, year=year,
                shared_drive=shared_drive, min_size_mb=min_size_mb, max_size_mb=max_size_mb,
                min_duration_sec=min_duration_sec, max_duration_sec=max_duration_sec,
                has_audio=has_audio, asset_type=asset_type, workflow_stage=workflow_stage,
                label=label, tracking=tracking, assignee=assignee,
            ),
            sort_by=sort_by,
            sort_dir=sort_dir,
            page=page,
            page_size=page_size,
            use_fts=semantic,
        )

        file_ids = [item.file_id for item in result.items]
        labels_map = repo.get_labels_map(file_ids)
        display_names = repo.get_user_display_names()
        latest_label_edits = repo.get_latest_label_edits(file_ids)
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
                    "labels": labels_map.get(item.file_id, []),
                    "is_new": not bool(item.reviewed_at),
                    "completeness": _video_completeness(
                        item.to_dict(),
                        labels_map.get(item.file_id, []),
                    ),
                    "last_label_edit": latest_label_edits.get(item.file_id),
                    "assigned_user_name": display_names.get(
                        item.assigned_user_email, item.assigned_user_email
                    ),
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
        payload["lexicon_terms"] = repo.get_video_lexicon_terms(file_id)
        payload["related_videos"] = repo.get_related_videos(file_id)
        payload["labels"] = repo.get_video_labels(file_id)
        payload["label_history"] = repo.get_label_history(file_id)
        payload["last_label_edit"] = (
            payload["label_history"][0] if payload["label_history"] else None
        )
        payload["assigned_user_name"] = repo.get_user_display_names().get(
            item.assigned_user_email, item.assigned_user_email
        )
        payload["is_new"] = not bool(item.reviewed_at)
        payload["completeness"] = _video_completeness(payload, payload["labels"])
        return payload

    @app.post("/api/titles/fill")
    async def fill_titles(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Fill every empty editorial title at once.

        Defaults to a dry run: the caller must pass apply=true explicitly, so a
        mass edit is always previewed before it is written.
        """
        _require_writable(settings)
        if settings.auth_required and not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return await asyncio.to_thread(
            repo.fill_missing_editorial_titles,
            apply=bool(payload.get("apply")),
            include_partial=payload.get("include_partial", True) is not False,
        )

    @app.get("/api/videos/{file_id}/title-suggestion")
    async def suggest_title(file_id: str) -> dict[str, Any]:
        """Propose a title. Read-only: nothing is saved until the user saves."""
        suggestion = repo.suggest_editorial_title(file_id)
        if not suggestion:
            raise HTTPException(status_code=404, detail="Video not found")
        return suggestion

    @app.put("/api/videos/{file_id}/metadata")
    async def update_video_metadata(file_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        _require_writable(settings)
        item = repo.update_christian_metadata(file_id, payload)
        if not item:
            raise HTTPException(status_code=404, detail="Video not found")
        data = item.to_dict()
        data["file_size_human"] = format_bytes(item.file_size)
        data["duration_human"] = format_duration(item.duration_seconds)
        data["lexicon_terms"] = repo.get_video_lexicon_terms(file_id)
        return data

    @app.put("/api/videos/{file_id}/workflow")
    async def update_video_workflow(file_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        _require_writable(settings)
        try:
            item = repo.update_workflow(file_id, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not item:
            raise HTTPException(status_code=404, detail="Video not found")
        data = item.to_dict()
        data["file_size_human"] = format_bytes(item.file_size)
        data["duration_human"] = format_duration(item.duration_seconds)
        data["related_videos"] = repo.get_related_videos(file_id)
        return data

    @app.put("/api/videos/{file_id}/labels")
    async def update_video_labels(
        request: Request,
        file_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _require_writable(settings)
        raw_labels = payload.get("labels", [])
        if not isinstance(raw_labels, list):
            raise HTTPException(status_code=400, detail="labels must be a list")
        user = request.state.user
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")
        labels = repo.set_video_labels(
            file_id,
            [str(label) for label in raw_labels],
            user_id=int(user["id"]),
            user_email=str(user["email"]),
        )
        if labels is None:
            raise HTTPException(status_code=404, detail="Video not found")
        history = repo.get_label_history(file_id)
        return {
            "file_id": file_id,
            "labels": labels,
            "label_history": history,
            "last_label_edit": history[0] if history else None,
        }

    @app.get("/api/export/inventory.xlsx")
    async def export_inventory(
        q: str = Query(default=""),
        folder: str = Query(default=""),
        extension: str = Query(default=""),
        resolution: str = Query(default=""),
        year: str = Query(default=""),
        shared_drive: str = Query(default=""),
        min_size_mb: float | None = Query(default=None),
        max_size_mb: float | None = Query(default=None),
        min_duration_sec: float | None = Query(default=None),
        max_duration_sec: float | None = Query(default=None),
        has_audio: bool | None = Query(default=None),
        asset_type: str = Query(default=""),
        workflow_stage: str = Query(default=""),
        label: str = Query(default=""),
        tracking: str = Query(default=""),
        assignee: str = Query(default=""),
    ) -> Response:
        """
        Multi-sheet Excel report. The Inventaire sheet honours the current
        dashboard filters; the Statistiques, Doublons, Arborescence and Erreurs
        sheets always describe the whole library.
        """
        filters = _search_filters(
            q=q, folder=folder, extension=extension, resolution=resolution, year=year,
            shared_drive=shared_drive, min_size_mb=min_size_mb, max_size_mb=max_size_mb,
            min_duration_sec=min_duration_sec, max_duration_sec=max_duration_sec,
            has_audio=has_audio, asset_type=asset_type, workflow_stage=workflow_stage,
            label=label, tracking=tracking, assignee=assignee,
        )

        def build() -> bytes:
            buffer = io.BytesIO()
            export_to_stream(repo, buffer, filters)
            return buffer.getvalue()

        payload = await asyncio.to_thread(build)
        return Response(
            content=payload,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{_export_filename("xlsx")}"'},
        )

    @app.get("/api/export/videos.csv")
    async def export_videos_csv(
        q: str = Query(default=""),
        folder: str = Query(default=""),
        extension: str = Query(default=""),
        resolution: str = Query(default=""),
        year: str = Query(default=""),
        shared_drive: str = Query(default=""),
        min_size_mb: float | None = Query(default=None),
        max_size_mb: float | None = Query(default=None),
        min_duration_sec: float | None = Query(default=None),
        max_duration_sec: float | None = Query(default=None),
        has_audio: bool | None = Query(default=None),
        asset_type: str = Query(default=""),
        workflow_stage: str = Query(default=""),
        label: str = Query(default=""),
        tracking: str = Query(default=""),
        assignee: str = Query(default=""),
    ) -> StreamingResponse:
        """Flat CSV of the filtered videos, streamed so large libraries do not buffer."""
        filters = _search_filters(
            q=q, folder=folder, extension=extension, resolution=resolution, year=year,
            shared_drive=shared_drive, min_size_mb=min_size_mb, max_size_mb=max_size_mb,
            min_duration_sec=min_duration_sec, max_duration_sec=max_duration_sec,
            has_audio=has_audio, asset_type=asset_type, workflow_stage=workflow_stage,
            label=label, tracking=tracking, assignee=assignee,
        )

        def rows() -> Any:
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")

            def flush() -> str:
                value = buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)
                return value

            # UTF-8 BOM so Excel opens accented French headers correctly.
            yield "\ufeff"
            writer.writerow(CSV_COLUMNS)
            yield flush()
            display_names = repo.get_user_display_names()
            for video in repo.iter_videos(filters):
                labels = repo.get_video_labels(video.file_id)
                writer.writerow(_csv_row(video, labels, display_names))
                yield flush()

        return StreamingResponse(
            rows(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{_export_filename("csv")}"'},
        )

    return app


CSV_COLUMNS = [
    "id_interne",
    "nom_fichier",
    "titre_editorial",
    "dossier",
    "lecteur_partage",
    "type_actif",
    "etape_production",
    "labels",
    "responsable",
    "intervenant",
    "predicateur",
    "theme_principal",
    "type_contenu",
    "evenement",
    "date_evenement",
    "serie",
    "langue",
    "taille_octets",
    "duree_secondes",
    "resolution",
    "codec_video",
    "codec_audio",
    "extension",
    "proprietaire",
    "cree_le",
    "modifie_le",
    "lien_drive",
]


def _csv_row(video: Any, labels: list[dict[str, Any]], display_names: dict[str, str] | None = None) -> list[Any]:
    return [
        video.internal_video_id,
        video.file_name,
        video.editorial_title,
        video.folder_path,
        video.shared_drive_name,
        video.asset_type,
        video.workflow_stage,
        ", ".join(str(label["name"]) for label in labels),
        (display_names or {}).get(video.assigned_user_email, video.assigned_user_email),
        video.speaker,
        video.preacher,
        video.main_theme,
        video.content_type,
        video.event_name,
        video.event_date,
        video.series_name,
        video.language,
        video.file_size,
        video.duration_seconds if video.duration_seconds is not None else "",
        video.resolution,
        video.video_codec,
        video.audio_codec,
        video.file_extension,
        video.owner,
        video.created_at,
        video.modified_at,
        video.drive_url,
    ]


def _export_filename(suffix: str) -> str:
    return f"cmfi-inventaire-video-{datetime.now(UTC).strftime('%Y%m%d-%H%M')}.{suffix}"


def _search_filters(**kwargs: Any) -> SearchFilters:
    """Build SearchFilters from the query parameters shared by search and export."""
    return SearchFilters(
        query=kwargs["q"],
        folder=kwargs["folder"],
        extension=kwargs["extension"],
        resolution=kwargs["resolution"],
        year=kwargs["year"],
        shared_drive=kwargs["shared_drive"],
        min_size_mb=kwargs["min_size_mb"],
        max_size_mb=kwargs["max_size_mb"],
        min_duration_sec=kwargs["min_duration_sec"],
        max_duration_sec=kwargs["max_duration_sec"],
        has_audio=kwargs["has_audio"],
        asset_type=kwargs["asset_type"],
        workflow_stage=kwargs["workflow_stage"],
        label=kwargs["label"],
        tracking=kwargs["tracking"],
        assignee=kwargs.get("assignee", ""),
    )


def _client_ip(request: Request, settings: Settings) -> str:
    """
    Best-effort client IP.

    On Render the app sits behind a proxy, so X-Forwarded-For carries the real
    address. The header is spoofable, which is why the e-mail counter — which
    an attacker cannot rotate when targeting one account — is the primary
    defence and this is only a second layer.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _login_identifiers(
    request: Request,
    settings: Settings,
    email: str,
) -> list[tuple[str, str]]:
    identifiers: list[tuple[str, str]] = []
    normalized_email = email.strip().casefold()
    if normalized_email:
        identifiers.append(("email", normalized_email))
    ip = _client_ip(request, settings)
    if ip:
        identifiers.append(("ip", ip))
    return identifiers


_IDLE_SCAN_STATE: dict[str, Any] = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "videos_found": 0,
    "videos_indexed": 0,
    "videos_skipped": 0,
    "folders_scanned": 0,
    "cuts_linked": 0,
    "errors": 0,
    "message": "",
}


def _initial_scan_state(repo: VideoRepository) -> dict[str, Any]:
    """
    Restore the last persisted scan state on startup.

    A scan cannot survive a process restart, so a state still marked "running"
    belongs to a scan that was killed mid-flight (a Render deploy, an OOM). It
    is reported as interrupted rather than silently resetting to idle, which
    would claim a half-finished scan had never happened.
    """
    state = dict(_IDLE_SCAN_STATE)
    stored = repo.get_scan_state()
    if not stored:
        return state
    state.update({key: stored[key] for key in state if key in stored})
    if state["status"] == "running":
        state["status"] = "interrupted"
        state["finished_at"] = state["finished_at"] or _utc_now()
        state["message"] = (
            "Le scan précédent a été interrompu par un redémarrage du service. "
            "Relancez-le pour terminer l'indexation."
        )
        repo.set_scan_state(state)
    return state


def _update_scan_state(
    repo: VideoRepository,
    scan_state: dict[str, Any],
    changes: dict[str, Any],
) -> None:
    scan_state.update(changes)
    try:
        repo.set_scan_state(scan_state)
    except Exception:
        # Progress reporting must never break the scan itself.
        LOGGER.exception("Could not persist scan state")


def _require_writable(settings: Settings) -> None:
    if settings.read_only:
        raise HTTPException(status_code=403, detail="Public demo is read-only")


def _require_drive_scan(
    repo: VideoRepository,
    settings: Settings,
    user: dict[str, Any] | None,
) -> str:
    _require_writable(settings)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    folder_id = _configured_scan_folder_id(repo, settings)
    if not folder_id:
        raise HTTPException(
            status_code=503,
            detail="Le dossier Drive de référence n'est pas configuré",
        )
    if not settings.google_service_account_json.strip() and not settings.google_credentials_path.exists():
        raise HTTPException(status_code=503, detail="Google Drive credentials are not configured")
    return folder_id


def _configured_scan_folder_id(repo: VideoRepository, settings: Settings) -> str:
    stored = repo.get_drive_folder_setting()
    if stored and stored.get("folder_id"):
        return str(stored["folder_id"])
    return _extract_folder_id(settings.drive_scan_folder_id.strip()) or ""


def _drive_folder_payload(repo: VideoRepository, settings: Settings) -> dict[str, Any]:
    stored = repo.get_drive_folder_setting()
    if stored and stored.get("folder_id"):
        return {"configured": True, "source": "application", **stored}
    folder_id = _extract_folder_id(settings.drive_scan_folder_id.strip()) or ""
    return {
        "configured": bool(folder_id),
        "source": "environment" if folder_id else "",
        "folder_id": folder_id,
        "folder_name": "",
        "folder_url": (
            f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else ""
        ),
        "updated_by_email": "",
        "updated_at": "",
        "last_scan_at": "",
        "last_scan_status": "",
    }


def _is_super_admin(settings: Settings, email: str) -> bool:
    return bool(settings.admin_email) and email.strip().casefold() == settings.admin_email.strip().casefold()


def _require_super_admin(
    settings: Settings,
    user: dict[str, Any] | None,
) -> dict[str, Any]:
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not _is_super_admin(settings, str(user["email"])):
        raise HTTPException(status_code=403, detail="Accès réservé au super-administrateur")
    return user


def _drive_service(settings: Settings) -> Any:
    credentials = authenticate(
        settings.google_credentials_path,
        settings.google_token_path,
        service_account_json=settings.google_service_account_json,
        allow_interactive=False,
    )
    return build_drive_service(credentials)


def _drive_service_or_http_error(settings: Settings) -> Any:
    try:
        return _drive_service(settings)
    except Exception as exc:
        LOGGER.exception("Google Drive authentication failed")
        raise HTTPException(
            status_code=503,
            detail="Impossible de se connecter à Google Drive. Vérifiez le compte de service.",
        ) from exc


async def _resolve_drive_folder(settings: Settings, folder_id: str) -> dict[str, Any]:
    service = _drive_service_or_http_error(settings)
    try:
        item = await asyncio.to_thread(
            lambda: service.files()
            .get(
                fileId=folder_id,
                fields="id,name,mimeType,webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
    except Exception as exc:
        LOGGER.exception("Drive folder access test failed for folder_id=%s", folder_id)
        raise HTTPException(
            status_code=400,
            detail="Le compte de service ne peut pas accéder à ce dossier",
        ) from exc
    if item.get("mimeType") != FOLDER_MIME:
        raise HTTPException(status_code=400, detail="L'élément Drive sélectionné n'est pas un dossier")
    return {
        "folder_id": item["id"],
        "folder_name": item.get("name", item["id"]),
        "folder_url": item.get("webViewLink")
        or f"https://drive.google.com/drive/folders/{item['id']}",
    }


async def _run_drive_scan(
    settings: Settings,
    repo: VideoRepository,
    folder_id: str,
    scan_lock: asyncio.Lock,
    scan_state: dict[str, Any],
) -> None:
    try:
        result = await asyncio.to_thread(run_scan, settings, full=False, folder_id=folder_id)
        _update_scan_state(
            repo,
            scan_state,
            {
                "status": "succeeded",
                "finished_at": _utc_now(),
                "videos_found": result.videos_found,
                "videos_indexed": result.videos_indexed,
                "videos_skipped": result.videos_skipped,
                "folders_scanned": result.folders_scanned,
                "cuts_linked": result.cuts_linked,
                "errors": result.errors,
                "message": "Scan terminé",
            },
        )
        repo.record_drive_scan(status="succeeded", scanned_at=str(scan_state["finished_at"]))
    except Exception:
        LOGGER.exception("Online Drive scan failed")
        _update_scan_state(
            repo,
            scan_state,
            {
                "status": "failed",
                "finished_at": _utc_now(),
                "message": "Le scan Drive a échoué. Vérifiez la configuration Render.",
            },
        )
        repo.record_drive_scan(status="failed", scanned_at=str(scan_state["finished_at"]))
    finally:
        scan_lock.release()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _video_completeness(
    video: dict[str, Any],
    labels: list[dict[str, Any]],
) -> dict[str, Any]:
    checks = {
        "type_et_statut_confirmes": bool(video.get("reviewed_at")),
        "titre": bool(str(video.get("editorial_title") or "").strip()),
        "theme": bool(str(video.get("main_theme") or "").strip()),
        "intervenant": bool(
            str(video.get("speaker") or video.get("preacher") or "").strip()
        ),
        "label": bool(labels),
    }
    completed = sum(checks.values())
    if completed == len(checks):
        status = "complete"
        label = "Complète"
    elif completed <= 1:
        status = "missing"
        label = "À compléter"
    else:
        status = "partial"
        label = "Partiellement renseignée"
    return {
        "status": status,
        "label": label,
        "completed": completed,
        "total": len(checks),
        "missing": [key for key, present in checks.items() if not present],
    }


def _session_response(
    auth_repo: AuthRepository,
    settings: Settings,
    user: dict[str, Any],
) -> JSONResponse:
    token = auth_repo.create_session(int(user["id"]), settings.session_days)
    response = JSONResponse({"ok": True, "user": user})
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


def _send_verification_code(
    auth_repo: AuthRepository,
    email_sender: EmailSender,
    settings: Settings,
    email: str,
    *,
    enforce_cooldown: bool = False,
) -> None:
    user, code = auth_repo.create_verification_code(
        email,
        duration_minutes=settings.email_verification_minutes,
        enforce_cooldown=enforce_cooldown,
    )
    try:
        email_sender.send_verification_code(str(user["email"]), code)
    except EmailDeliveryError:
        auth_repo.delete_verification_code(int(user["id"]))
        raise


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
