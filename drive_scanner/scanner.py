from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tqdm import tqdm

from database.models import VideoRecord
from database.repository import VideoRepository
from drive_scanner.client import build_drive_service
from metadata.ffprobe_extractor import FfprobeExtractor
from classification.christian_enricher import enrich_from_context
from utils.config import Settings
from utils.retry import execute_with_retry
from utils.video_formats import VIDEO_EXTENSIONS

LOGGER = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

FILE_FIELDS = (
    "nextPageToken, files("
    "id, name, mimeType, parents, size, createdTime, modifiedTime, "
    "owners(displayName,emailAddress), shortcutDetails, driveId, webViewLink"
    ")"
)

DRIVE_FIELDS = "nextPageToken, drives(id, name)"


@dataclass(slots=True)
class ScanResult:
    videos_found: int = 0
    videos_indexed: int = 0
    videos_skipped: int = 0
    folders_scanned: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FolderRef:
    folder_id: str
    drive_id: str | None = None
    shared_drive_name: str = ""


class DriveScanner:
    def __init__(
        self,
        settings: Settings,
        repo: VideoRepository,
        creds: Any,
    ) -> None:
        self.settings = settings
        self.repo = repo
        self.service = build_drive_service(creds)
        self.ffprobe = FfprobeExtractor(settings) if settings.enable_ffprobe else None

        self._folder_names: dict[str, str] = {"root": "My Drive"}
        self._folder_parents: dict[str, str | None] = {"root": None}
        self._drive_names: dict[str, str] = {}

    def scan(self, *, full: bool = False, folder_id: str | None = None) -> ScanResult:
        result = ScanResult()
        incremental = self.settings.incremental and not full
        modified_map = {} if not incremental else self.repo.get_modified_map()

        if folder_id:
            roots = [self._resolve_folder_ref(folder_id)]
        else:
            roots = self._collect_roots()
        queue: deque[FolderRef] = deque(roots)
        visited: set[str] = set()

        pbar = tqdm(desc="Scanning Drive", unit="folder", dynamic_ncols=True)

        while queue:
            folder = queue.popleft()
            if folder.folder_id in visited:
                continue
            visited.add(folder.folder_id)
            result.folders_scanned += 1
            pbar.set_postfix(videos=result.videos_found, folders=result.folders_scanned)
            pbar.update(1)

            try:
                for item in self._list_children(folder):
                    mime = item.get("mimeType", "")

                    if mime == FOLDER_MIME:
                        self._cache_folder(item)
                        queue.append(
                            FolderRef(
                                folder_id=item["id"],
                                drive_id=folder.drive_id or item.get("driveId"),
                                shared_drive_name=folder.shared_drive_name,
                            )
                        )
                        continue

                    if mime == SHORTCUT_MIME and self.settings.follow_shortcuts:
                        details = item.get("shortcutDetails") or {}
                        target = details.get("targetId")
                        target_mime = details.get("targetMimeType", "")
                        if target and target_mime == FOLDER_MIME:
                            queue.append(
                                FolderRef(
                                    folder_id=target,
                                    drive_id=folder.drive_id,
                                    shared_drive_name=folder.shared_drive_name,
                                )
                            )
                        elif target and self._is_video_mime_or_name(target_mime, item.get("name", "")):
                            self._process_video(
                                item,
                                folder,
                                modified_map,
                                incremental,
                                result,
                                resolved_id=target,
                                resolved_mime=target_mime,
                            )
                        continue

                    if self._is_video_item(item):
                        self._process_video(item, folder, modified_map, incremental, result)

            except Exception as exc:
                result.errors += 1
                msg = f"Folder {folder.folder_id}: {exc}"
                result.error_messages.append(msg)
                LOGGER.exception(msg)

        pbar.close()
        LOGGER.info(
            "Scan complete: %s videos found, %s indexed, %s skipped, %s errors",
            result.videos_found,
            result.videos_indexed,
            result.videos_skipped,
            result.errors,
        )
        return result

    def _collect_roots(self) -> list[FolderRef]:
        roots = [FolderRef(folder_id="root", shared_drive_name="")]

        if self.settings.include_shared_drives:
            page_token: str | None = None
            while True:
                response = execute_with_retry(
                    lambda pt=page_token: self.service.drives().list(
                        pageSize=100,
                        pageToken=pt,
                        fields=DRIVE_FIELDS,
                    ).execute()
                )
                for drive in response.get("drives", []):
                    drive_id = drive["id"]
                    name = drive.get("name", drive_id)
                    self._drive_names[drive_id] = name
                    self._folder_names[drive_id] = name
                    self._folder_parents[drive_id] = None
                    roots.append(
                        FolderRef(folder_id=drive_id, drive_id=drive_id, shared_drive_name=name)
                    )
                page_token = response.get("nextPageToken")
                if not page_token:
                    break

        return roots

    def _list_children(self, folder: FolderRef) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            response = execute_with_retry(lambda pt=page_token: self._do_list(folder, pt))
            items.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return items

    def _do_list(self, folder: FolderRef, page_token: str | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "q": f"'{folder.folder_id}' in parents and trashed=false",
            "pageSize": 1000,
            "pageToken": page_token,
            "fields": FILE_FIELDS,
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }
        if folder.drive_id:
            kwargs["corpora"] = "drive"
            kwargs["driveId"] = folder.drive_id
        else:
            # Critical for folders outside "My Drive" (e.g. Shared drives, Shared with me).
            kwargs["corpora"] = "allDrives"
        return self.service.files().list(**kwargs).execute()

    def _resolve_folder_ref(self, folder_id: str) -> FolderRef:
        """
        Resolve a folder ID into a scan root. This is important for:
        - Shared Drives (need driveId+corpora=drive for best results)
        - Shared with me (often needs corpora=allDrives)
        """
        meta = execute_with_retry(
            lambda: self.service.files()
            .get(
                fileId=folder_id,
                fields="id,name,driveId",
                supportsAllDrives=True,
            )
            .execute()
        )
        drive_id = meta.get("driveId")
        folder_name = meta.get("name", folder_id)
        shared_drive_name = ""

        if drive_id:
            if drive_id not in self._drive_names:
                try:
                    drive = execute_with_retry(
                        lambda: self.service.drives().get(driveId=drive_id, fields="id,name").execute()
                    )
                    self._drive_names[drive_id] = drive.get("name", drive_id)
                except Exception:
                    self._drive_names[drive_id] = drive_id
            shared_drive_name = self._drive_names.get(drive_id, drive_id)

        # Cache the folder itself so path reconstruction works.
        self._folder_names[folder_id] = folder_name
        return FolderRef(folder_id=folder_id, drive_id=drive_id, shared_drive_name=shared_drive_name or "Shared")

    def _cache_folder(self, item: dict[str, Any]) -> None:
        folder_id = item["id"]
        self._folder_names[folder_id] = item.get("name", folder_id)
        parents = item.get("parents") or []
        self._folder_parents[folder_id] = parents[0] if parents else None

    def _folder_path(self, item: dict[str, Any]) -> tuple[str, str]:
        parents = item.get("parents") or []
        if not parents:
            return "", ""
        parent_id = parents[0]
        segments: list[str] = []
        current: str | None = parent_id
        seen: set[str] = set()

        while current and current not in seen:
            seen.add(current)
            name = self._folder_names.get(current)
            if name is None:
                self._fetch_folder_metadata(current)
                name = self._folder_names.get(current, current)
            if current == "root":
                break
            segments.append(name)
            current = self._folder_parents.get(current)
            if current == "root":
                segments.append(self._folder_names.get("root", "My Drive"))
                break

        segments.reverse()
        full_path = "/" + "/".join(segments) if segments else ""
        parent_name = segments[-1] if segments else ""
        return full_path, parent_name

    def _fetch_folder_metadata(self, folder_id: str) -> None:
        try:
            meta = execute_with_retry(
                lambda: self.service.files()
                .get(
                    fileId=folder_id,
                    fields="id, name, parents",
                    supportsAllDrives=True,
                )
                .execute()
            )
            self._folder_names[folder_id] = meta.get("name", folder_id)
            parents = meta.get("parents") or []
            self._folder_parents[folder_id] = parents[0] if parents else None
        except Exception as exc:
            LOGGER.debug("Could not fetch folder %s: %s", folder_id, exc)
            self._folder_names[folder_id] = folder_id

    def _is_video_item(self, item: dict[str, Any]) -> bool:
        return self._is_video_mime_or_name(item.get("mimeType", ""), item.get("name", ""))

    def _is_video_mime_or_name(self, mime: str, name: str) -> bool:
        if mime.startswith("video/"):
            return True
        return Path(name).suffix.lower() in VIDEO_EXTENSIONS

    def _process_video(
        self,
        item: dict[str, Any],
        folder: FolderRef,
        modified_map: dict[str, str],
        incremental: bool,
        result: ScanResult,
        *,
        resolved_id: str | None = None,
        resolved_mime: str | None = None,
    ) -> None:
        result.videos_found += 1
        file_id = resolved_id or item["id"]
        modified_at = item.get("modifiedTime", "")

        if incremental and modified_map.get(file_id) == modified_at:
            result.videos_skipped += 1
            return

        folder_path, parent_folder = self._folder_path(item)
        file_name = item.get("name", file_id)
        ext = Path(file_name).suffix.lower()
        owners = item.get("owners") or []
        owner = ""
        if owners:
            owner = owners[0].get("emailAddress") or owners[0].get("displayName") or ""

        record = VideoRecord(
            file_id=file_id,
            file_name=file_name,
            drive_url=item.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view",
            folder_path=folder_path,
            parent_folder=parent_folder,
            file_size=int(item.get("size") or 0),
            mime_type=resolved_mime or item.get("mimeType", ""),
            owner=owner,
            created_at=item.get("createdTime", ""),
            modified_at=modified_at,
            shared_drive_name=folder.shared_drive_name,
            file_extension=ext,
            scan_status="indexed",
            last_scanned_at=datetime.now(timezone.utc).isoformat(),
        )

        # Deterministic enrichment (safe, fast) based on filename + folder structure.
        enrichment = enrich_from_context(file_name=file_name, folder_path=folder_path)
        record.clean_title = enrichment.clean_title
        record.normalized_name = enrichment.normalized_name
        record.speaker = enrichment.speaker
        record.ministry = enrichment.ministry
        record.main_theme = enrichment.main_theme
        record.biblical_topics = "; ".join(enrichment.biblical_topics)
        record.bible_references = "; ".join(enrichment.bible_references)
        record.teaching_type = enrichment.teaching_type
        record.keywords = "; ".join(enrichment.keywords)
        record.semantic_tags = "; ".join(enrichment.semantic_tags)

        if self.ffprobe and self._should_probe(record):
            try:
                meta = self.ffprobe.extract(self.service, file_id, file_name)
                record.duration_seconds = meta.get("duration_seconds")
                record.width = meta.get("width")
                record.height = meta.get("height")
                record.resolution = meta.get("resolution", "")
                record.fps = meta.get("fps")
                record.video_codec = meta.get("video_codec", "")
                record.audio_codec = meta.get("audio_codec", "")
                record.bitrate = meta.get("bitrate")
                record.aspect_ratio = meta.get("aspect_ratio", "")
                record.orientation = meta.get("orientation", "")
                record.has_audio = meta.get("has_audio")
                record.container_format = meta.get("container_format", "")
            except Exception as exc:
                record.scan_status = "metadata_error"
                record.error_message = str(exc)
                result.errors += 1
                result.error_messages.append(f"{file_name}: {exc}")
                LOGGER.warning("ffprobe failed for %s: %s", file_name, exc)
        try:
            self.repo.upsert_video(record)
            result.videos_indexed += 1
            modified_map[file_id] = modified_at
        except Exception as exc:
            result.errors += 1
            result.error_messages.append(f"{file_name}: {exc}")
            LOGGER.exception("Failed to save %s", file_name)

    def _should_probe(self, record: VideoRecord) -> bool:
        if not self.ffprobe:
            return False
        max_mb = self.settings.max_download_mb
        if max_mb <= 0:
            return False
        size_mb = record.file_size / (1024 * 1024) if record.file_size else 0
        return size_mb <= max_mb
