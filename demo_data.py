from __future__ import annotations

from database.models import VideoRecord
from database.repository import VideoRepository


def seed_public_demo(repo: VideoRepository) -> None:
    samples = [
        VideoRecord(
            file_id="demo-raw-001",
            file_name="K7-001-convention-yaounde-brut.mp4",
            editorial_title="Convention de Yaounde - Session d'ouverture",
            folder_path="/Dpt DIGITAL/Demo/Videos brutes",
            parent_folder="Videos brutes",
            file_size=4_800_000_000,
            mime_type="video/mp4",
            modified_at="2025-10-14T10:30:00Z",
            file_extension=".mp4",
            resolution="720x576",
            asset_type="raw",
            workflow_stage="digitized",
            main_theme="Consecration",
            speaker="Orateur demo",
        ),
        VideoRecord(
            file_id="demo-raw-002",
            file_name="K7-002-priere-brut.mp4",
            editorial_title="La puissance de la priere",
            folder_path="/Dpt DIGITAL/Demo/Videos brutes",
            parent_folder="Videos brutes",
            file_size=3_600_000_000,
            mime_type="video/mp4",
            modified_at="2025-11-02T08:15:00Z",
            file_extension=".mp4",
            resolution="720x576",
            asset_type="raw",
            workflow_stage="to_review",
            main_theme="Priere",
        ),
        VideoRecord(
            file_id="demo-raw-003",
            file_name="K7-003-foi-brut.mp4",
            editorial_title="Marcher par la foi",
            folder_path="/Dpt DIGITAL/Demo/Videos brutes",
            parent_folder="Videos brutes",
            file_size=3_950_000_000,
            mime_type="video/mp4",
            modified_at="2025-11-18T14:00:00Z",
            file_extension=".mp4",
            resolution="720x576",
            asset_type="raw",
            workflow_stage="transcribed",
            main_theme="Foi",
            transcript_status="complete",
            transcript_summary="Enseignement de demonstration sur la foi chretienne.",
        ),
        VideoRecord(
            file_id="demo-cut-001",
            file_name="K7-001-extrait-appel.mp4",
            editorial_title="L'appel a la consecration",
            folder_path="/Dpt DIGITAL/Demo/Videos decoupees",
            parent_folder="Videos decoupees",
            file_size=720_000_000,
            mime_type="video/mp4",
            modified_at="2026-01-09T09:20:00Z",
            file_extension=".mp4",
            resolution="1920x1080",
            asset_type="cut",
            workflow_stage="treated",
            source_file_id="demo-raw-001",
            main_theme="Consecration",
            workflow_notes="Image stabilisee et audio nettoye.",
        ),
        VideoRecord(
            file_id="demo-cut-002",
            file_name="K7-002-extrait-intercession.mp4",
            editorial_title="Perseverer dans l'intercession",
            folder_path="/Dpt DIGITAL/Demo/Videos decoupees",
            parent_folder="Videos decoupees",
            file_size=640_000_000,
            mime_type="video/mp4",
            modified_at="2026-01-22T16:45:00Z",
            file_extension=".mp4",
            resolution="1920x1080",
            asset_type="cut",
            workflow_stage="ready_edit",
            source_file_id="demo-raw-002",
            main_theme="Priere",
        ),
        VideoRecord(
            file_id="demo-cut-003",
            file_name="K7-003-extrait-foi.mp4",
            editorial_title="La foi vient de ce qu'on entend",
            folder_path="/Dpt DIGITAL/Demo/Publications",
            parent_folder="Publications",
            file_size=510_000_000,
            mime_type="video/mp4",
            modified_at="2026-02-04T12:00:00Z",
            file_extension=".mp4",
            resolution="1920x1080",
            asset_type="cut",
            workflow_stage="published",
            source_file_id="demo-raw-003",
            main_theme="Foi",
            bible_references="Romains 10:17",
        ),
    ]

    for video in samples:
        repo.upsert_video(video)

    repo.set_video_labels(
        "demo-raw-002",
        ["A visionner", "Prioritaire"],
        user_id=None,
        user_email="demo@local",
    )
    repo.set_video_labels(
        "demo-cut-001",
        ["Audio nettoye", "Image restauree"],
        user_id=None,
        user_email="demo@local",
    )
    repo.set_video_labels(
        "demo-cut-003",
        ["Publiee", "Extrait court"],
        user_id=None,
        user_email="demo@local",
    )
