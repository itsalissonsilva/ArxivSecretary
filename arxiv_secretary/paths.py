from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


APP_DIR_NAME = "ArxivSecretary"
DB_NAME = "arxiv_secretary.db"


def app_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates = [Path(local_app_data) / APP_DIR_NAME]
    else:
        candidates = []

    candidates.extend(
        [
            Path.home() / ".arxiv_secretary",
            Path(tempfile.gettempdir()) / APP_DIR_NAME,
        ]
    )

    for target in candidates:
        try:
            target.mkdir(parents=True, exist_ok=True)
            return target
        except OSError:
            continue

    raise OSError("Could not create an application data directory.")


def database_path() -> Path:
    legacy_path = Path.cwd() / DB_NAME
    if _should_use_legacy_database(legacy_path):
        return legacy_path
    return app_data_dir() / DB_NAME


def _should_use_legacy_database(legacy_path: Path) -> bool:
    if not legacy_path.exists():
        return False

    if getattr(sys, "frozen", False):
        return False

    repo_root = Path.cwd()
    if not (repo_root / "main.py").exists():
        return False
    if not (repo_root / "arxiv_secretary").is_dir():
        return False

    return True
