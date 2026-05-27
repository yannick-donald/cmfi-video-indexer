from __future__ import annotations

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


def build_drive_service(creds: Credentials):
    return build("drive", "v3", credentials=creds, cache_discovery=False)
