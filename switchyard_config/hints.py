"""Model hint definitions for the configurator's Models tab.

Hints are small TOML "definition files" that describe what selected model
families can do. Each entry carries a regex matched against the model id
and a ``suggest`` map of ``extra_body`` defaults (typically capability
flags like ``supports_images = true``). The Models tab shows matching
suggestions as one-click "Apply" values.

The definitions ship baked into the configurator image
(``configurator/model_hints.toml``). A Kubernetes deployment can override
them by setting ``MODEL_HINTS_FILE`` to a ConfigMap-mounted file (the chart
does this when ``values.modelHints`` is set).

File format::

    [[hint]]
    pattern = '(?i)qwen3[._-]?8'          # regex, searched against model ids
    description = "Qwen3.8 family: ...'   # shown next to matching models
    suggest = { supports_images = true }  # extra_body defaults to offer

The first entry whose ``pattern`` matches wins (put specific families
before generic ones).
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path

from .constants import PROJECT_ROOT

# Baked-in definitions; overridden via env (ConfigMap mount in Kubernetes).
MODEL_HINTS_FILE = os.environ.get(
    "MODEL_HINTS_FILE", str(PROJECT_ROOT / "configurator" / "model_hints.toml")
)

# Sanity caps so a bad file can't blow up the UI.
_MAX_HINTS = 200
_MAX_SUGGEST_KEYS = 16


def load_model_hints(path: str | None = None) -> dict:
    """Load and validate the hint definitions file.

    Returns ``{"ok": bool, "hints": [...], "source": str, "error": str?}``.
    A missing file is not an error (hints are optional) — it yields an
    empty list. Malformed entries are skipped and reported in ``error``
    so one bad line never hides the good ones.
    """
    p = Path(path or MODEL_HINTS_FILE)
    if not p.exists():
        return {"ok": True, "hints": [], "source": str(p), "error": None}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "hints": [], "source": str(p), "error": str(e)}

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return {"ok": False, "hints": [], "source": str(p), "error": f"{p.name}: {e}"}

    hints: list[dict] = []
    problems: list[str] = []
    raw = data.get("hint", [])
    if not isinstance(raw, list):
        return {"ok": False, "hints": [], "source": str(p),
                "error": f"{p.name}: [[hint]] entries must be an array of tables"}
    for i, entry in enumerate(raw[:_MAX_HINTS]):
        if not isinstance(entry, dict):
            continue
        pattern = str(entry.get("pattern", ""))
        try:
            re.compile(pattern)
        except re.error as e:
            problems.append(f"hint[{i}] pattern: {e}")
            continue
        suggest = entry.get("suggest", {})
        if not isinstance(suggest, dict) or len(suggest) > _MAX_SUGGEST_KEYS:
            problems.append(f"hint[{i}] suggest: must be a table of at most "
                            f"{_MAX_SUGGEST_KEYS} keys")
            continue
        hints.append({
            "pattern": pattern,
            "description": str(entry.get("description", "")),
            "suggest": dict(suggest),
        })
    if len(raw) > _MAX_HINTS:
        problems.append(f"only the first {_MAX_HINTS} hints are used")

    out = {"ok": not problems, "hints": hints, "source": str(p)}
    if problems:
        out["error"] = "; ".join(problems)
    return out


def match_hint(model_id: str, hints: list[dict]) -> dict | None:
    """Return the first hint whose pattern searches against *model_id*."""
    for h in hints:
        try:
            if re.search(h.get("pattern", r"(?!)"), model_id or ""):
                return h
        except re.error:
            continue
    return None


def extras_display(value: object) -> str:
    """Render an extra_body value for a UI chip (compact, lossless enough)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)