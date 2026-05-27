from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class VideoRecord:
    file_id: str
    file_name: str
    internal_video_id: str = ""
    drive_url: str = ""
    folder_path: str = ""
    parent_folder: str = ""
    file_size: int = 0
    mime_type: str = ""
    owner: str = ""
    created_at: str = ""
    modified_at: str = ""
    shared_drive_name: str = ""
    clean_title: str = ""
    normalized_name: str = ""
    speaker: str = ""
    ministry: str = ""
    main_theme: str = ""
    biblical_topics: str = ""
    bible_references: str = ""
    teaching_type: str = ""
    ai_summary: str = ""
    keywords: str = ""
    semantic_tags: str = ""
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    resolution: str = ""
    fps: float | None = None
    video_codec: str = ""
    audio_codec: str = ""
    bitrate: int | None = None
    aspect_ratio: str = ""
    orientation: str = ""
    has_audio: bool | None = None
    container_format: str = ""
    file_extension: str = ""
    scan_status: str = "indexed"
    last_scanned_at: str = ""
    error_message: str = ""

    @classmethod
    def from_row(cls, row: Any) -> VideoRecord:
        return cls(
            file_id=row["file_id"],
            file_name=row["file_name"],
            internal_video_id=row["internal_video_id"] or "",
            drive_url=row["drive_url"] or "",
            folder_path=row["folder_path"] or "",
            parent_folder=row["parent_folder"] or "",
            file_size=row["file_size"] or 0,
            mime_type=row["mime_type"] or "",
            owner=row["owner"] or "",
            created_at=row["created_at"] or "",
            modified_at=row["modified_at"] or "",
            shared_drive_name=row["shared_drive_name"] or "",
            clean_title=row["clean_title"] or "",
            normalized_name=row["normalized_name"] or "",
            speaker=row["speaker"] or "",
            ministry=row["ministry"] or "",
            main_theme=row["main_theme"] or "",
            biblical_topics=row["biblical_topics"] or "",
            bible_references=row["bible_references"] or "",
            teaching_type=row["teaching_type"] or "",
            ai_summary=row["ai_summary"] or "",
            keywords=row["keywords"] or "",
            semantic_tags=row["semantic_tags"] or "",
            duration_seconds=row["duration_seconds"],
            width=row["width"],
            height=row["height"],
            resolution=row["resolution"] or "",
            fps=row["fps"],
            video_codec=row["video_codec"] or "",
            audio_codec=row["audio_codec"] or "",
            bitrate=row["bitrate"],
            aspect_ratio=row["aspect_ratio"] or "",
            orientation=row["orientation"] or "",
            has_audio=bool(row["has_audio"]) if row["has_audio"] is not None else None,
            container_format=row["container_format"] or "",
            file_extension=row["file_extension"] or "",
            scan_status=row["scan_status"] or "indexed",
            last_scanned_at=row["last_scanned_at"] or "",
            error_message=row["error_message"] or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "file_name": self.file_name,
            "internal_video_id": self.internal_video_id,
            "drive_url": self.drive_url,
            "folder_path": self.folder_path,
            "parent_folder": self.parent_folder,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "owner": self.owner,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "shared_drive_name": self.shared_drive_name,
            "clean_title": self.clean_title,
            "normalized_name": self.normalized_name,
            "speaker": self.speaker,
            "ministry": self.ministry,
            "main_theme": self.main_theme,
            "biblical_topics": self.biblical_topics,
            "bible_references": self.bible_references,
            "teaching_type": self.teaching_type,
            "ai_summary": self.ai_summary,
            "keywords": self.keywords,
            "semantic_tags": self.semantic_tags,
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
            "resolution": self.resolution,
            "fps": self.fps,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "bitrate": self.bitrate,
            "aspect_ratio": self.aspect_ratio,
            "orientation": self.orientation,
            "has_audio": self.has_audio,
            "container_format": self.container_format,
            "file_extension": self.file_extension,
            "scan_status": self.scan_status,
            "last_scanned_at": self.last_scanned_at,
            "error_message": self.error_message,
        }
