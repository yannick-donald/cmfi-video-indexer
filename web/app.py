from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from auth.email_sender import EmailDeliveryError, EmailSender
from database.auth import AuthError, AuthRepository
from database.repository import SearchFilters, VideoRepository
from demo_data import seed_public_demo
from drive_scanner.runner import run_scan
from utils.config import Settings
from utils.formatters import format_bytes, format_duration

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def create_app(settings: Settings) -> FastAPI:
    settings.ensure_dirs()
    repo = VideoRepository(settings.db_path)
    auth_repo = AuthRepository(settings.db_path)
    email_sender = EmailSender(settings)
    if settings.auto_seed_demo and repo.get_stats()["total_videos"] == 0:
        seed_public_demo(repo)
    if settings.admin_email and settings.admin_password:
        auth_repo.ensure_user(settings.admin_email, settings.admin_password)

    app = FastAPI(title="Google Drive Video Library", version="1.0.0")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def require_authentication(request: Request, call_next: Any) -> Any:
        public_path = (
            request.url.path == "/health"
            or request.url.path == "/login"
            or request.url.path.startswith("/api/auth/")
            or request.url.path.startswith("/static/")
        )
        token = request.cookies.get(settings.session_cookie_name, "")
        user = auth_repo.get_session_user(token) if token else None
        if user:
            user["can_scan_drive"] = _can_scan_drive(settings, str(user["email"]))
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
    async def login(payload: dict[str, Any]) -> JSONResponse:
        try:
            user = auth_repo.authenticate(
                str(payload.get("email") or ""),
                str(payload.get("password") or ""),
            )
        except AuthError as exc:
            if "confirmer votre adresse" in str(exc):
                return JSONResponse(
                    {
                        "detail": str(exc),
                        "verification_required": True,
                        "email": str(payload.get("email") or "").strip().casefold(),
                    },
                    status_code=403,
                )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not user:
            raise HTTPException(status_code=401, detail="E-mail ou mot de passe incorrect")
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
            },
        )

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
        return repo.get_workflow_stats()

    @app.get("/api/workflow/raw-videos")
    async def raw_video_options(exclude_file_id: str = Query(default="")) -> dict[str, Any]:
        return {"items": repo.get_raw_video_options(exclude_file_id)}

    @app.post("/api/scan-folder")
    async def scan_folder(request: Request, payload: dict[str, Any]) -> JSONResponse:
        _require_drive_scan(settings, request.state.user)
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
        asset_type: str = Query(default=""),
        workflow_stage: str = Query(default=""),
        label: str = Query(default=""),
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
                asset_type=asset_type,
                workflow_stage=workflow_stage,
                label=label,
            ),
            sort_by=sort_by,
            sort_dir=sort_dir,
            page=page,
            page_size=page_size,
            use_fts=semantic,
        )

        labels_map = repo.get_labels_map([item.file_id for item in result.items])
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
        return payload

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
    async def update_video_labels(file_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        _require_writable(settings)
        raw_labels = payload.get("labels", [])
        if not isinstance(raw_labels, list):
            raise HTTPException(status_code=400, detail="labels must be a list")
        labels = repo.set_video_labels(file_id, [str(label) for label in raw_labels])
        if labels is None:
            raise HTTPException(status_code=404, detail="Video not found")
        return {"file_id": file_id, "labels": labels}

    return app


def _require_writable(settings: Settings) -> None:
    if settings.read_only:
        raise HTTPException(status_code=403, detail="Public demo is read-only")


def _require_drive_scan(settings: Settings, user: dict[str, Any] | None) -> None:
    _require_writable(settings)
    if not user or not user.get("can_scan_drive"):
        raise HTTPException(status_code=403, detail="Drive scan permission required")
    if not settings.google_service_account_json.strip() and not settings.google_credentials_path.exists():
        raise HTTPException(status_code=503, detail="Google Drive credentials are not configured")


def _can_scan_drive(settings: Settings, email: str) -> bool:
    allowed = {
        item.strip().casefold()
        for item in settings.drive_scan_emails.split(",")
        if item.strip()
    }
    return email.strip().casefold() in allowed


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
