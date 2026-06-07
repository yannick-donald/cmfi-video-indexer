from __future__ import annotations

from utils.config import Settings
from web.app import create_app


app = create_app(Settings())
