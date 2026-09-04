"""Provider display names and sidecar metadata I/O."""

from __future__ import annotations

import json
from pathlib import Path

from .constants import SELF_PROVIDER_NAME
from .models import Provider


# Known provider local-name -> display-name mappings. Used as smart defaults
# when the user hasn't set a custom display_name. Names not in this dict fall
# back to title-casing the local name (with underscores → spaces).
_KNOWN_PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "vllm": "vLLM",
    "ollama": "Ollama",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "lm_studio": "LM Studio",
    "lmstudio": "LM Studio",
    "together": "Together AI",
    "groq": "Groq",
    "mistral": "Mistral",
    "deepseek": "DeepSeek",
    "kimi": "Kimi",
    "local": "Local",
}


def provider_display_name(p: Provider) -> str:
    """Return a human-readable display name for *p*.

    Uses the explicit ``display_name`` field when set; otherwise derives a
    smart default from the local ``name`` via the known-provider table or
    title-casing.
    """
    if p.display_name.strip():
        return p.display_name.strip()
    return _KNOWN_PROVIDER_DISPLAY_NAMES.get(
        p.name.lower(),
        p.name.replace("_", " ").replace("-", " ").title(),
    )


def load_provider_meta(path: Path) -> dict[str, dict]:
    """Load the sidecar provider metadata JSON, returning {} if missing."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def apply_provider_meta(providers: list[Provider], path: Path) -> None:
    """Apply display names from the sidecar metadata file to *providers*."""
    apply_provider_meta_dict(providers, load_provider_meta(path))


def apply_provider_meta_dict(providers: list[Provider], meta: dict) -> None:
    """Apply display names from a metadata dict (e.g. from a ConfigMap)."""
    for p in providers:
        entry = meta.get(p.name)
        if isinstance(entry, dict) and entry.get("display_name"):
            p.display_name = entry["display_name"]


def provider_meta_json(providers: list[Provider]) -> str:
    """Serialize provider display names to the sidecar JSON format.

    Mirrors :func:`save_provider_meta`: only providers with a non-default
    display name are kept, so the result stays minimal. Returns ``""`` when
    there is nothing to persist.
    """
    meta: dict[str, dict] = {}
    for p in providers:
        if p.name == SELF_PROVIDER_NAME:
            continue
        # Only persist when the user set something non-default.
        if p.display_name.strip():
            meta[p.name] = {"display_name": p.display_name.strip()}
    if not meta:
        return ""
    return json.dumps(meta, indent=2, sort_keys=True) + "\n"


def save_provider_meta(providers: list[Provider], path: Path) -> None:
    """Write provider display names to the sidecar metadata file.

    Only providers with a non-empty display_name that differs from the
    smart default are persisted, to keep the file minimal. When no
    providers have a custom display_name, the file is removed rather
    than holding an empty ``{}`` — nothing to rotate or back up on
    subsequent saves.
    """
    text = provider_meta_json(providers)
    if not text:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)