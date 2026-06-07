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
    editorial_title: str = ""
    original_title: str = ""
    alternate_titles: str = ""
    normalized_name: str = ""
    speaker: str = ""
    preacher: str = ""
    ministry: str = ""
    main_theme: str = ""
    spiritual_themes: str = ""
    doctrine_topics: str = ""
    biblical_topics: str = ""
    bible_references: str = ""
    songs: str = ""
    worship_leaders: str = ""
    content_type: str = ""
    event_name: str = ""
    event_date: str = ""
    location: str = ""
    language: str = ""
    audience: str = ""
    series_name: str = ""
    session_number: str = ""
    teaching_type: str = ""
    ai_summary: str = ""
    transcript_status: str = ""
    transcript_text_path: str = ""
    transcript_summary: str = ""
    manual_notes: str = ""
    metadata_source: str = ""
    metadata_confidence: float | None = None
    metadata_updated_at: str = ""
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
            editorial_title=row["editorial_title"] or "",
            original_title=row["original_title"] or "",
            alternate_titles=row["alternate_titles"] or "",
            normalized_name=row["normalized_name"] or "",
            speaker=row["speaker"] or "",
            preacher=row["preacher"] or "",
            ministry=row["ministry"] or "",
            main_theme=row["main_theme"] or "",
            spiritual_themes=row["spiritual_themes"] or "",
            doctrine_topics=row["doctrine_topics"] or "",
            biblical_topics=row["biblical_topics"] or "",
            bible_references=row["bible_references"] or "",
            songs=row["songs"] or "",
            worship_leaders=row["worship_leaders"] or "",
            content_type=row["content_type"] or "",
            event_name=row["event_name"] or "",
            event_date=row["event_date"] or "",
            location=row["location"] or "",
            language=row["language"] or "",
            audience=row["audience"] or "",
            series_name=row["series_name"] or "",
            session_number=row["session_number"] or "",
            teaching_type=row["teaching_type"] or "",
            ai_summary=row["ai_summary"] or "",
            transcript_status=row["transcript_status"] or "",
            transcript_text_path=row["transcript_text_path"] or "",
            transcript_summary=row["transcript_summary"] or "",
            manual_notes=row["manual_notes"] or "",
            metadata_source=row["metadata_source"] or "",
            metadata_confidence=row["metadata_confidence"],
            metadata_updated_at=row["metadata_updated_at"] or "",
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
            "editorial_title": self.editorial_title,
            "original_title": self.original_title,
            "alternate_titles": self.alternate_titles,
            "normalized_name": self.normalized_name,
            "speaker": self.speaker,
            "preacher": self.preacher,
            "ministry": self.ministry,
            "main_theme": self.main_theme,
            "spiritual_themes": self.spiritual_themes,
            "doctrine_topics": self.doctrine_topics,
            "biblical_topics": self.biblical_topics,
            "bible_references": self.bible_references,
            "songs": self.songs,
            "worship_leaders": self.worship_leaders,
            "content_type": self.content_type,
            "event_name": self.event_name,
            "event_date": self.event_date,
            "location": self.location,
            "language": self.language,
            "audience": self.audience,
            "series_name": self.series_name,
            "session_number": self.session_number,
            "teaching_type": self.teaching_type,
            "ai_summary": self.ai_summary,
            "transcript_status": self.transcript_status,
            "transcript_text_path": self.transcript_text_path,
            "transcript_summary": self.transcript_summary,
            "manual_notes": self.manual_notes,
            "metadata_source": self.metadata_source,
            "metadata_confidence": self.metadata_confidence,
            "metadata_updated_at": self.metadata_updated_at,
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
