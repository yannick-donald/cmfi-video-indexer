from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Sequence

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow

LOGGER = logging.getLogger(__name__)


DRIVE_SCOPES: Sequence[str] = (
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    # Needed to download files for ffprobe metadata (optional).
    "https://www.googleapis.com/auth/drive.readonly",
)


class AuthError(RuntimeError):
    pass


def _safe_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_credentials(token_path: Path) -> Credentials | None:
    if not token_path.exists():
        return None
    try:
        return Credentials.from_authorized_user_file(str(token_path), scopes=DRIVE_SCOPES)
    except Exception as e:  # pragma: no cover
        LOGGER.warning("Failed to read token file; will re-authenticate", extra={"error": str(e)})
        return None


def save_credentials(token_path: Path, creds: Credentials) -> None:
    _safe_write_json(token_path, json.loads(creds.to_json()))


def authenticate(
    credentials_path: Path,
    token_path: Path,
    scopes: Sequence[str] = DRIVE_SCOPES,
    service_account_json: str = "",
    allow_interactive: bool = True,
) -> Credentials:
    if service_account_json.strip():
        try:
            payload = json.loads(service_account_json)
            return service_account.Credentials.from_service_account_info(
                payload,
                scopes=list(scopes),
            )
        except Exception as e:
            raise AuthError(f"Service account authentication failed: {e}") from e

    if not credentials_path.exists():
        raise AuthError(
            f"Missing OAuth client credentials JSON at '{credentials_path}'. "
            "Download it from Google Cloud Console and place it there."
        )

    creds = load_credentials(token_path)
    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(token_path, creds)
            return creds
        except Exception as e:
            LOGGER.warning("Token refresh failed; re-authenticating", extra={"error": str(e)})

    if not allow_interactive:
        raise AuthError(
            "No usable Google token is available. Configure a service account "
            "for the hosted application."
        )

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes=list(scopes))
        creds = flow.run_local_server(port=0, open_browser=True)
        save_credentials(token_path, creds)
        return creds
    except Exception as e:
        raise AuthError(f"OAuth authentication failed: {e}") from e
