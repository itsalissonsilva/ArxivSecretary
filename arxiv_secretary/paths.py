from __future__ import annotations

import os
from pathlib import Path


APP_DIR_NAME = "ArxivSecretary"
DB_NAME = "arxiv_secretary.db"


def app_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        target = Path(local_app_data) / APP_DIR_NAME
    else:
        target = Path.home() / ".arxiv_secretary"
    target.mkdir(parents=True, exist_ok=True)
    return target


def database_path() -> Path:
    legacy_path = Path.cwd() / DB_NAME
    if legacy_path.exists():
        return legacy_path
    return app_data_dir() / DB_NAME
