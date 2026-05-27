from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaIoBaseDownload

from utils.config import Settings
from utils.retry import execute_with_retry

LOGGER = logging.getLogger(__name__)


class FfprobeExtractor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache_dir = settings.cache_dir / "downloads"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ffprobe = self._resolve_ffprobe(settings.ffmpeg_bin_dir)

    def extract(self, service: Any, file_id: str, file_name: str) -> dict[str, Any]:
        local_path = self._download(service, file_id, file_name)
        try:
            return self._probe(local_path)
        finally:
            if local_path.exists():
                local_path.unlink(missing_ok=True)

    def _resolve_ffprobe(self, bin_dir: str) -> str:
        if bin_dir:
            candidate = Path(bin_dir) / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if candidate.exists():
                return str(candidate)
        found = shutil.which("ffprobe")
        if not found:
            raise FileNotFoundError(
                "ffprobe not found. Install FFmpeg or set FFMPEG_BIN_DIR in .env"
            )
        return found

    def _download(self, service: Any, file_id: str, file_name: str) -> Path:
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in file_name)
        dest = self.cache_dir / f"{file_id}_{safe_name}"
        if dest.exists():
            return dest

        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        buffer = BytesIO()
        downloader = MediaIoBaseDownload(buffer, request, chunksize=1024 * 1024)
        done = False
        while not done:
            _, done = execute_with_retry(lambda: downloader.next_chunk())

        dest.write_bytes(buffer.getvalue())
        return dest

    def _probe(self, path: Path) -> dict[str, Any]:
        cmd = [
            self.ffprobe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return self._parse_probe(data)

    def _parse_probe(self, data: dict[str, Any]) -> dict[str, Any]:
        streams = data.get("streams", [])
        fmt = data.get("format", {})
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})

        width = video.get("width")
        height = video.get("height")
        resolution = f"{width}x{height}" if width and height else ""

        fps = None
        rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
        if rate and "/" in str(rate):
            num, den = str(rate).split("/", 1)
            if float(den) != 0:
                fps = round(float(num) / float(den), 2)

        duration = fmt.get("duration")
        duration_seconds = float(duration) if duration else None

        orientation = ""
        if width and height:
            orientation = "portrait" if height > width else "landscape"

        aspect_ratio = ""
        if width and height:
            from math import gcd

            g = gcd(int(width), int(height))
            aspect_ratio = f"{int(width) // g}:{int(height) // g}"

        bitrate = None
        if fmt.get("bit_rate"):
            bitrate = int(fmt["bit_rate"])

        return {
            "duration_seconds": duration_seconds,
            "width": width,
            "height": height,
            "resolution": resolution,
            "fps": fps,
            "video_codec": video.get("codec_name", ""),
            "audio_codec": audio.get("codec_name", ""),
            "bitrate": bitrate,
            "aspect_ratio": aspect_ratio,
            "orientation": orientation,
            "has_audio": bool(audio),
            "container_format": fmt.get("format_name", ""),
        }
