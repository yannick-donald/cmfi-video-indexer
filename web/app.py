from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
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
from auth.drive_auth import authenticate
from drive_scanner.client import build_drive_service
from drive_scanner.scanner import FOLDER_MIME
from drive_scanner.runner import run_scan
from utils.config import Settings
from utils.formatters import format_bytes, format_duration

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
LOGGER = logging.getLogger(__name__)


def create_app(settings: Settings) -> FastAPI:
    settings.ensure_dirs()
    repo = VideoRepository(settings.db_path)
    auth_repo = AuthRepository(settings.db_path)
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
    scan_state: dict[str, Any] = {
        "status": "idle",
        "started_at": None,
        "finished_at": None,
        "videos_found": 0,
        "videos_indexed": 0,
        "videos_skipped": 0,
        "folders_scanned": 0,
        "errors": 0,
        "message": "",
    }
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
                "drive_scan_configured": bool(_configured_scan_folder_id(repo, settings)),
                "drive_folder": _drive_folder_payload(repo, settings),
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
        return {**repo.get_workflow_stats(), "tracking": repo.get_tracking_stats()}

    @app.get("/api/workflow/raw-videos")
    async def raw_video_options(exclude_file_id: str = Query(default="")) -> dict[str, Any]:
        return {"items": repo.get_raw_video_options(exclude_file_id)}

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
        scan_state.update(
            {
                "status": "running",
                "started_at": _utc_now(),
                "finished_at": None,
                "videos_found": 0,
                "videos_indexed": 0,
                "videos_skipped": 0,
                "folders_scanned": 0,
                "errors": 0,
                "message": "Analyse du dossier Drive en cours",
            }
        )
        task = asyncio.create_task(_run_drive_scan(settings, folder_id, scan_lock, scan_state))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return JSONResponse(dict(scan_state), status_code=202)

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
                tracking=tracking,
            ),
            sort_by=sort_by,
            sort_dir=sort_dir,
            page=page,
            page_size=page_size,
            use_fts=semantic,
        )

        file_ids = [item.file_id for item in result.items]
        labels_map = repo.get_labels_map(file_ids)
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
        payload["is_new"] = not bool(item.reviewed_at)
        payload["completeness"] = _video_completeness(payload, payload["labels"])
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

    return app


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
    folder_id: str,
    scan_lock: asyncio.Lock,
    scan_state: dict[str, Any],
) -> None:
    try:
        result = await asyncio.to_thread(run_scan, settings, full=False, folder_id=folder_id)
        scan_state.update(
            {
                "status": "succeeded",
                "finished_at": _utc_now(),
                "videos_found": result.videos_found,
                "videos_indexed": result.videos_indexed,
                "videos_skipped": result.videos_skipped,
                "folders_scanned": result.folders_scanned,
                "errors": result.errors,
                "message": "Scan terminé",
            }
        )
        VideoRepository(settings.db_path).record_drive_scan(
            status="succeeded",
            scanned_at=str(scan_state["finished_at"]),
        )
    except Exception:
        LOGGER.exception("Online Drive scan failed")
        scan_state.update(
            {
                "status": "failed",
                "finished_at": _utc_now(),
                "message": "Le scan Drive a échoué. Vérifiez la configuration Render.",
            }
        )
        VideoRepository(settings.db_path).record_drive_scan(
            status="failed",
            scanned_at=str(scan_state["finished_at"]),
        )
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
