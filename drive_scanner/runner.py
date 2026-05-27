from __future__ import annotations

import logging

from auth.drive_auth import authenticate
from database.repository import VideoRepository
from drive_scanner.scanner import DriveScanner, ScanResult
from utils.config import Settings

LOGGER = logging.getLogger(__name__)


def run_scan(settings: Settings, *, full: bool = False, folder_id: str | None = None) -> ScanResult:
    settings.ensure_dirs()
    creds = authenticate(settings.google_credentials_path, settings.google_token_path)
    repo = VideoRepository(settings.db_path)
    scanner = DriveScanner(settings, repo, creds)
    LOGGER.info(
        "Starting Google Drive scan (incremental=%s, folder_id=%s)",
        settings.incremental and not full,
        folder_id,
    )
    return scanner.scan(full=full, folder_id=folder_id)
