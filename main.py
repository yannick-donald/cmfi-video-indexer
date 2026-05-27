from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from database.models import VideoRecord
from database.repository import VideoRepository
from drive_scanner.runner import run_scan
from utils.config import Settings
from utils.logging import configure_logging
from web.app import create_app


def seed_demo_data(repo: VideoRepository) -> None:
    samples = [
        VideoRecord(
            file_id="demo-001",
            file_name="Intro to Python.mp4",
            drive_url="https://drive.google.com/file/d/demo-001/view",
            folder_path="/Courses/Python",
            parent_folder="Python",
            file_size=524_288_000,
            mime_type="video/mp4",
            owner="you@example.com",
            created_at="2024-01-10T10:00:00Z",
            modified_at="2024-03-15T12:30:00Z",
            duration_seconds=3600,
            width=1920,
            height=1080,
            resolution="1920x1080",
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
            bitrate=4_500_000,
            aspect_ratio="16:9",
            orientation="landscape",
            has_audio=True,
            container_format="mp4",
            file_extension=".mp4",
        ),
        VideoRecord(
            file_id="demo-002",
            file_name="Advanced Editing.mov",
            drive_url="https://drive.google.com/file/d/demo-002/view",
            folder_path="/Media/Projects",
            parent_folder="Projects",
            file_size=1_073_741_824,
            mime_type="video/quicktime",
            owner="you@example.com",
            created_at="2023-08-02T08:00:00Z",
            modified_at="2025-01-20T09:15:00Z",
            duration_seconds=7200,
            width=3840,
            height=2160,
            resolution="3840x2160",
            fps=24.0,
            video_codec="hevc",
            audio_codec="aac",
            bitrate=12_000_000,
            aspect_ratio="16:9",
            orientation="landscape",
            has_audio=True,
            container_format="mov",
            file_extension=".mov",
        ),
        VideoRecord(
            file_id="demo-003",
            file_name="Team Standup Recording.webm",
            drive_url="https://drive.google.com/file/d/demo-003/view",
            folder_path="/Meetings/2025",
            parent_folder="2025",
            file_size=157_286_400,
            mime_type="video/webm",
            owner="team@example.com",
            created_at="2025-02-01T14:00:00Z",
            modified_at="2025-02-01T15:00:00Z",
            duration_seconds=1800,
            width=1280,
            height=720,
            resolution="1280x720",
            fps=25.0,
            video_codec="vp9",
            audio_codec="opus",
            bitrate=1_200_000,
            aspect_ratio="16:9",
            orientation="landscape",
            has_audio=True,
            container_format="webm",
            file_extension=".webm",
            shared_drive_name="Company Drive",
        ),
    ]
    for video in samples:
        repo.upsert_video(video)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Google Drive Video Inventory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan Google Drive and index videos into SQLite")
    scan_parser.add_argument("--full", action="store_true", help="Ignore incremental cache and rescan all videos")

    run_parser = subparsers.add_parser("run", help="Scan Drive then open the web dashboard")
    run_parser.add_argument("--full", action="store_true", help="Full rescan (not incremental)")
    run_parser.add_argument("--no-serve", action="store_true", help="Scan only, do not start web UI")
    run_parser.add_argument("--host", default=None)
    run_parser.add_argument("--port", type=int, default=None)

    serve_parser = subparsers.add_parser("serve", help="Launch the local web search dashboard")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=None)
    serve_parser.add_argument("--reload", action="store_true")

    subparsers.add_parser("seed-demo", help="Insert demo videos for testing the web UI")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    settings = Settings()
    settings.ensure_dirs()
    configure_logging(settings.log_level, Path("logs/app.log"))
    logger = logging.getLogger("main")

    if args.command == "seed-demo":
        repo = VideoRepository(settings.db_path)
        seed_demo_data(repo)
        logger.info("Demo videos inserted into %s", settings.db_path)
        return 0

    if args.command == "scan":
        result = run_scan(settings, full=args.full)
        logger.info(
            "Indexed %s videos (%s skipped, %s errors). Open dashboard: python main.py serve",
            result.videos_indexed,
            result.videos_skipped,
            result.errors,
        )
        return 0

    if args.command == "run":
        result = run_scan(settings, full=args.full)
        logger.info(
            "Scan done: %s indexed, %s skipped, %s errors",
            result.videos_indexed,
            result.videos_skipped,
            result.errors,
        )
        if args.no_serve:
            return 0
        host = args.host or settings.web_host
        port = args.port or settings.web_port
        app = create_app(settings)
        logger.info("Starting web dashboard at http://%s:%s", host, port)
        uvicorn.run(app, host=host, port=port, log_level=settings.log_level.lower())
        return 0

    if args.command == "serve":
        host = args.host or settings.web_host
        port = args.port or settings.web_port
        app = create_app(settings)
        logger.info("Starting web dashboard at http://%s:%s", host, port)
        uvicorn.run(
            app,
            host=host,
            port=port,
            reload=args.reload,
            log_level=settings.log_level.lower(),
        )
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
