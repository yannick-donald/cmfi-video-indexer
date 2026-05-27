from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="google-drive-video-inventory", alias="APP_NAME")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    google_credentials_path: Path = Field(default=Path("credentials.json"), alias="GOOGLE_CREDENTIALS_PATH")
    google_token_path: Path = Field(default=Path("cache/token.json"), alias="GOOGLE_TOKEN_PATH")

    db_path: Path = Field(default=Path("database/index.sqlite3"), alias="DB_PATH")
    output_dir: Path = Field(default=Path("output"), alias="OUTPUT_DIR")
    excel_output_path: Path = Field(
        default=Path("output/google_drive_video_inventory.xlsx"), alias="EXCEL_OUTPUT_PATH"
    )
    cache_dir: Path = Field(default=Path("cache"), alias="CACHE_DIR")

    include_shared_drives: bool = Field(default=True, alias="INCLUDE_SHARED_DRIVES")
    include_shared_with_me: bool = Field(default=True, alias="INCLUDE_SHARED_WITH_ME")
    follow_shortcuts: bool = Field(default=True, alias="FOLLOW_SHORTCUTS")

    enable_ffprobe: bool = Field(default=True, alias="ENABLE_FFPROBE")
    ffmpeg_bin_dir: str = Field(default="", alias="FFMPEG_BIN_DIR")
    max_download_mb: int = Field(default=0, alias="MAX_DOWNLOAD_MB")

    download_workers: int = Field(default=4, alias="DOWNLOAD_WORKERS")
    ffprobe_workers: int = Field(default=6, alias="FFPROBE_WORKERS")

    incremental: bool = Field(default=True, alias="INCREMENTAL")

    web_host: str = Field(default="127.0.0.1", alias="WEB_HOST")
    web_port: int = Field(default=8080, alias="WEB_PORT")

    def ensure_dirs(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        Path("logs").mkdir(parents=True, exist_ok=True)

