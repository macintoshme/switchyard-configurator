"""Draft-file autosave and timestamped backup rotation for the configurator."""

from __future__ import annotations

import datetime
from pathlib import Path

from .constants import (
    ENV_DRAFT,
    MAX_BACKUPS,
    PROVIDER_META_DRAFT,
    ROUTES_DRAFT,
)
from .models import ConfigState
from .providers import save_provider_meta
from .routes import generate_toml, update_env_file


def rotate_backups(path: Path, max_backups: int = MAX_BACKUPS) -> Path | None:
    """Rotate *path* to a timestamped ``.bak`` file, keeping the last *max_backups*.

    If *path* exists, it is copied to ``<name>.<timestamp>.bak``. If a backup
    with that timestamp already exists (e.g. two saves in the same second), a
    numeric suffix is appended. Older ``.bak`` files (sorted by name, which
    sorts chronologically due to the timestamp) are deleted until only
    *max_backups* remain. Returns the new backup path, or ``None`` if *path*
    did not exist.
    """
    if not path.exists():
        return None
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    backup = path.parent / f"{path.name}.{ts}.bak"
    # Handle timestamp collisions (two saves in the same second)
    counter = 1
    while backup.exists():
        backup = path.parent / f"{path.name}.{ts}.{counter}.bak"
        counter += 1
    backup.write_text(path.read_text())
    # Sort by name (timestamp suffix keeps them chronological).
    backups = sorted(path.parent.glob(f"{path.name}.*.bak"))
    for old in backups[:-max_backups]:
        old.unlink(missing_ok=True)
    return backup


def write_draft(state: ConfigState) -> bool:
    """Write the current state to draft files (best-effort autosave).

    Returns ``True`` if both files were written successfully, ``False`` if an
    error occurred. Errors are swallowed so a failed draft never crashes the app.
    """
    try:
        ROUTES_DRAFT.parent.mkdir(parents=True, exist_ok=True)
        ROUTES_DRAFT.write_text(generate_toml(state))
        ENV_DRAFT.write_text(update_env_file(state.providers))
        save_provider_meta(state.providers, PROVIDER_META_DRAFT)
        return True
    except OSError:
        return False


def clear_draft() -> None:
    """Remove draft files after a successful save."""
    ROUTES_DRAFT.unlink(missing_ok=True)
    ENV_DRAFT.unlink(missing_ok=True)
    PROVIDER_META_DRAFT.unlink(missing_ok=True)