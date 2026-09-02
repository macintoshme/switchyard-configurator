"""opencode config integration, OpenRouter pricing, and file I/O."""

from __future__ import annotations

import datetime
import json
import json5
import re
import urllib.error
import urllib.request
from pathlib import Path

from .constants import (
    MAX_BACKUPS,
    OPENCODE_GLOBAL_DIR,
    OPENCODE_PROJECT_CONFIG,
    OPENROUTER_MODELS_URL,
    OPENROUTER_TIMEOUT,
    PROVIDER_META_DRAFT,
    ROUTES_DRAFT,
    ENV_DRAFT,
)
from .models import ConfigState, Route
from .providers import save_provider_meta
from .routes import generate_toml, update_env_file


# ---------------------------------------------------------------------------
# opencode config path and JSONC handling
# ---------------------------------------------------------------------------

def find_opencode_config_path() -> Path:
    """Return the path to the opencode config to write to.

    Prefers an existing project-local file, then an existing global file,
    then falls back to creating a project-local `opencode.json`.
    """
    if OPENCODE_PROJECT_CONFIG.exists():
        return OPENCODE_PROJECT_CONFIG
    for name in ("opencode.json", "opencode.jsonc"):
        candidate = OPENCODE_GLOBAL_DIR / name
        if candidate.exists():
            return candidate
    return OPENCODE_PROJECT_CONFIG


_JSONC_TOKEN_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"'  # string literals (skip these)
    r"|/\*.*?\*/"         # /* block comments */
    r"|//[^\n]*",         # // line comments
    re.DOTALL,
)


def _config_has_comments(text: str) -> bool:
    """Return True if *text* contains JSONC comments (``//`` or ``/* */``).

    Uses a tokenizer that respects string literals (a ``//`` inside a URL
    string is not a comment).
    """
    for m in _JSONC_TOKEN_RE.finditer(text):
        # String literals are matched too; only non-string matches are comments.
        if not m.group(0).startswith('"'):
            return True
    return False


def load_opencode_config(path: Path) -> dict:
    """Load an opencode.json[c] file, returning {} if missing or unparseable."""
    if not path.exists():
        return {}
    try:
        return json5.loads(path.read_text())
    except (ValueError, OSError):
        return {}


# ---------------------------------------------------------------------------
# OpenRouter pricing lookup
# ---------------------------------------------------------------------------
# Switchyard passthrough model IDs (e.g. "DeepSeek-V4-Flash-thinking-low")
# usually correspond to an upstream model offered on OpenRouter. We scrape
# OpenRouter's public /models endpoint for per-token USD pricing and attach
# a `cost` block (in $/Mtok, matching opencode's expected units) to each
# passthrough model entry in the opencode config.

# Suffixes switchyard adds to model IDs to distinguish thinking modes and
# tool-calling variants. Stripped before matching against OpenRouter IDs.
# Stacked suffixes (e.g. ``-thinking-high-legacy-tool-calling``) are handled
# by the ``+`` quantifier in the regex below.
_SWITCHYARD_MODEL_SUFFIX_RE = re.compile(
    r"(?:-legacy-tool-calling|-thinking-high|-thinking-low|-thinking-max|-non-thinking)+$"
)


def _normalize_model_for_match(s: str) -> str:
    """Normalize a model name for fuzzy matching against OpenRouter IDs.

    Lowercases, strips switchyard-specific suffixes (a model may carry
    several stacked, e.g. ``"glm-52-thinking-high-legacy-tool-calling"``),
    and removes every non-alphanumeric character so ``"GLM-5.2-thinking-high"``
    and ``"z-ai/glm-5.2"`` both reduce to a comparable form.
    """
    s = s.lower()
    s = _SWITCHYARD_MODEL_SUFFIX_RE.sub("", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def _strip_openrouter_variant(id_or_slug: str) -> str:
    """Strip OpenRouter alias/variant markers from a model ID.

    OpenRouter uses a leading ``~`` for alias IDs (e.g.
    ``~deepseek/deepseek-v4-flash-latest``) and a ``:variant`` suffix for
    tiers (``:free``, ``:batch``). We strip both so the canonical form
    (``deepseek/deepseek-v4-flash``) is what we compare.
    """
    s = id_or_slug
    if s.startswith("~"):
        s = s[1:]
    if ":" in s:
        s = s.split(":", 1)[0]
    return s


# Maps a token found in a switchyard model ID (e.g. ``"glm"`` in
# ``"GLM-5.2"``) to the OpenRouter vendor prefixes that might publish
# models with that token. Used to disambiguate substring matches when
# several OpenRouter vendors ship similarly-named models.
#
# Vendors not listed here are not disambiguated — substring matching
# still works, it just doesn't filter by vendor, so a model from an
# unknown vendor with a name overlapping a known one could
# occasionally match the wrong entry. Add entries here when a new
# upstream provider starts appearing in switchyard routes.
_VENDOR_ALIASES: dict[str, tuple[str, ...]] = {
    "deepseek": ("deepseek",),
    "glm": ("z-ai", "zhipu", "glm"),
    "kimi": ("moonshot", "kimi"),
    "gpt-oss": ("openai", "gpt-oss"),
    "qwen": ("qwen", "alibaba"),
    "llama": ("llama",),
    "mistral": ("mistral",),
}


def fetch_openrouter_pricing(
    timeout: float = OPENROUTER_TIMEOUT,
) -> list[dict]:
    """Fetch the OpenRouter public models list, returning the raw entries.

    Each entry includes ``id``, ``name``, ``canonical_slug``, and a
    ``pricing`` dict with per-token USD strings (``prompt``,
    ``completion``, ``input_cache_read``). Returns an empty list on any
    network or parse error so callers can degrade gracefully.
    """
    req = urllib.request.Request(
        OPENROUTER_MODELS_URL, headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return []
    models = data.get("data", []) if isinstance(data, dict) else data
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict)]


def _openrouter_cost_entry(model: dict) -> dict | None:
    """Convert an OpenRouter model entry's pricing to an opencode ``cost`` dict.

    OpenRouter prices are per-token USD strings; opencode expects $/Mtok
    floats. Returns ``None`` if the entry has no usable pricing (e.g. the
    ``:free`` tier, which reports ``"0"``). ``cache_read`` is omitted when
    OpenRouter reports ``null`` for ``input_cache_read`` (matching the
    existing provider entries that omit it for models without prompt
    caching).
    """
    pricing = model.get("pricing") or {}
    prompt = pricing.get("prompt")
    completion = pricing.get("completion")
    cache_read = pricing.get("input_cache_read")
    if prompt is None or completion is None:
        return None
    try:
        prompt_f = float(prompt)
        completion_f = float(completion)
    except (TypeError, ValueError):
        return None
    # Skip free-tier entries (0 prompt price) — they aren't real costs.
    if prompt_f <= 0 and completion_f <= 0:
        return None
    cost: dict[str, float] = {
        "input": round(prompt_f * 1_000_000, 6),
        "output": round(completion_f * 1_000_000, 6),
    }
    if cache_read is not None:
        try:
            cache_f = float(cache_read)
            if cache_f > 0:
                cost["cache_read"] = round(cache_f * 1_000_000, 6)
        except (TypeError, ValueError):
            pass
    return cost


def _openrouter_match_score(model: dict) -> tuple[int, int, int]:
    """Sort key ranking an OpenRouter entry for preferential matching.

    Lower is better. The tuple compares (variant_penalty, vision_penalty,
    dated_penalty) so the canonical non-vision entry wins over ``:free`` /
    ``:batch`` / ``vision-exp`` / ``~alias`` / version-pinned variants.
    """
    raw_id = model.get("id", "")
    canonical = model.get("canonical_slug", "") or raw_id
    stripped = _strip_openrouter_variant(raw_id)
    variant_penalty = 0 if raw_id == stripped else 1
    vision_penalty = 1 if "vision" in raw_id.lower() else 0
    # A date suffix like "-0731" or "-20260731" signals a version-pinned
    # snapshot rather than the canonical model.
    dated = bool(
        re.search(r"-\d{4,8}$", stripped)
        or (canonical != raw_id and re.search(r"-\d{8}$", canonical))
    )
    return (variant_penalty, vision_penalty, 1 if dated else 0)


def lookup_openrouter_cost(
    model_id: str, openrouter_models: list[dict]
) -> dict | None:
    """Find the best OpenRouter match for *model_id* and return its cost dict.

    Matching strategy (in priority order):

    1. Exact normalized match between the switchyard model ID (with
       switchyard suffixes stripped) and the OpenRouter ID (with alias /
       variant markers stripped).
    2. Substring match: the normalized switchyard ID appears in the
       normalized OpenRouter ID, filtered to entries whose vendor prefix
       (``deepseek/``, ``z-ai/``, ``openai/``, ``moonshotai/``, ...) lines
       up with a token in the switchyard ID.

    Among matching candidates, the entry with the lowest
    :func:`_openrouter_match_score` wins — i.e. the canonical non-vision
    non-alias entry is preferred over ``:free`` / ``:batch`` / ``vision``
    / ``~latest`` variants.

    Returns ``None`` if no match is found or the matched entry has no
    usable pricing.
    """
    if not model_id or not openrouter_models:
        return None
    needle = _normalize_model_for_match(model_id)
    if not needle:
        return None

    # Vendor token from the switchyard ID (e.g. "glm" from "GLM-5.2").
    # Used to disambiguate substring matches when several OpenRouter
    # vendors ship similarly-named models. See _VENDOR_ALIASES for the
    # limitation on unknown vendors.
    vendor_token = next(
        (token for token in _VENDOR_ALIASES if token in needle), ""
    )

    def candidate_ok(m: dict) -> bool:
        if not vendor_token:
            return True
        raw = m.get("id", "").lower() + " " + m.get("name", "").lower()
        aliases = _VENDOR_ALIASES[vendor_token]
        return any(a in raw for a in aliases)

    # Build (normalized_stripped_id, model) pairs once.
    indexed = []
    for m in openrouter_models:
        raw_id = m.get("id", "")
        if not raw_id:
            continue
        stripped = _strip_openrouter_variant(raw_id)
        norm = re.sub(r"[^a-z0-9]+", "", stripped.lower())
        indexed.append((norm, m))

    # 1. Exact normalized match.
    exact = [m for norm, m in indexed if norm == needle and candidate_ok(m)]
    if exact:
        best = min(exact, key=_openrouter_match_score)
        return _openrouter_cost_entry(best)

    # 2. Substring match (needle contained in normalized OpenRouter ID).
    substr = [m for norm, m in indexed if needle in norm and candidate_ok(m)]
    if substr:
        best = min(substr, key=_openrouter_match_score)
        return _openrouter_cost_entry(best)

    return None


# ---------------------------------------------------------------------------
# opencode provider entry building
# ---------------------------------------------------------------------------

def build_opencode_provider_entry(
    routes: list[Route], base_url: str,
    pricing: list[dict] | None = None,
) -> dict:
    """Build the opencode provider dict for the switchyard routes.

    Each route's `id` (e.g., `switchyard/escalation`) becomes a model entry.
    The local TOML name is used to build a human-readable display name.

    When *pricing* is a list of OpenRouter model entries (as returned by
    :func:`fetch_openrouter_pricing`), each **passthrough** route gets a
    ``cost`` block attached (in $/Mtok) looked up from OpenRouter by
    matching the route's model ID. Non-passthrough routes (classifiers,
    random, stage routers, advisors) are skipped — their effective cost
    depends on which target handles the request, so a single number would
    be misleading. Models without an OpenRouter match (e.g. a locally
    hosted vLLM model) simply get no ``cost`` block, preserving the
    current behavior.
    """
    models: dict[str, dict] = {}
    for route in routes:
        if not route.id:
            continue
        if route.name:
            display = route.name.replace("_", " ").replace("-", " ").title()
        else:
            display = route.id
        entry: dict[str, object] = {"name": f"Switchyard {display}"}
        # Only passthroughs map cleanly to a single upstream model whose
        # pricing OpenRouter would publish.
        if pricing and route.type == "passthrough":
            cost = lookup_openrouter_cost(route.id, pricing)
            if cost:
                entry["cost"] = cost
        models[route.id] = entry
    return {
        "npm": "@ai-sdk/openai-compatible",
        "name": "NeMo Switchyard",
        "options": {
            "baseURL": base_url,
            "apiKey": "sk-switchyard",
        },
        "models": models,
    }


def merge_opencode_provider_entry(
    existing: dict | None, new_entry: dict
) -> dict:
    """Merge *new_entry* into *existing* opencode provider entry.

    Preserves user customizations in *existing* while applying functional
    updates from *new_entry*:

    - ``npm`` and ``options.baseURL`` / ``options.apiKey``: always taken
      from *new_entry* (required for the switchyard integration to work).
    - ``name``: preserved from *existing* if the user set a custom one;
      otherwise taken from *new_entry*.
    - ``models``: merged. Models from *new_entry* (one per current route)
      are added or have their ``name`` updated; any extra model fields the
      user added beyond ``name`` / ``cost`` are preserved. Models in
      *existing* but not in *new_entry* are kept — a user may have
      manually registered a model that isn't backed by a switchyard route.

    ``cost`` is treated as a functional field (like ``name``): when
    *new_entry* provides one for a model, it overwrites whatever the
    existing entry had, so re-running "Add to opencode" refreshes
    OpenRouter pricing. When *new_entry* has no ``cost`` for a model
    (e.g. the OpenRouter lookup failed or there was no match), any
    existing ``cost`` is preserved so a transient outage doesn't strip
    pricing that was previously set.
    """
    if not existing or not isinstance(existing, dict):
        return new_entry

    merged = dict(existing)
    # Functional fields: always update from new_entry.
    merged["npm"] = new_entry["npm"]
    merged["options"] = dict(merged.get("options") or {})
    merged["options"]["baseURL"] = new_entry["options"]["baseURL"]
    merged["options"]["apiKey"] = new_entry["options"]["apiKey"]
    # Cosmetic field: keep user's custom name if set, else use new_entry's.
    if not merged.get("name"):
        merged["name"] = new_entry["name"]
    # Models: merge by route id. Existing user-added models (not backed by
    # a current route) are preserved.
    merged_models = dict(merged.get("models") or {})
    for model_id, new_model in new_entry["models"].items():
        existing_model = merged_models.get(model_id)
        if isinstance(existing_model, dict):
            # Update functional fields (name, cost) but keep any extra
            # fields the user added (temperature, top_p, ...).
            merged_model = dict(existing_model)
            merged_model["name"] = new_model["name"]
            if "cost" in new_model:
                merged_model["cost"] = new_model["cost"]
            elif "cost" in merged_model:
                # new_entry had no cost for this model — keep the prior
                # value rather than dropping pricing on a transient
                # OpenRouter lookup miss.
                pass
            else:
                merged_model.pop("cost", None)
            merged_models[model_id] = merged_model
        else:
            merged_models[model_id] = new_model
    merged["models"] = merged_models
    return merged


# ---------------------------------------------------------------------------
# File I/O: backups, drafts, opencode config writing
# ---------------------------------------------------------------------------

def write_opencode_config(path: Path, config: dict) -> Path | None:
    """Write the merged opencode config, backing up any existing file.

    If the existing file contains JSONC comments (which ``json.dumps``
    would destroy, since it only emits plain JSON), it's backed up with
    a ``.comments.bak`` suffix that's excluded from rotation — so the
    commented source is preserved indefinitely and the user can recover
    their comments. Files without comments are rotated via
    :func:`rotate_backups` (keep last 5) for consistency with the other
    config files.

    Returns the backup path if one was created, or ``None`` if not.
    """
    backup_path: Path | None = None
    if path.exists():
        original = path.read_text()
        if _config_has_comments(original):
            # Preserve the commented source indefinitely — comments can't
            # be reconstructed from the parsed JSON we're about to write.
            ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            backup_path = path.parent / f"{path.name}.{ts}.comments.bak"
            counter = 1
            while backup_path.exists():
                backup_path = path.parent / f"{path.name}.{ts}.{counter}.comments.bak"
                counter += 1
            backup_path.write_text(original)
        else:
            # No comments to preserve — rotate like other config files.
            backup_path = rotate_backups(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
    return backup_path


def rotate_backups(path: Path, max_backups: int = MAX_BACKUPS) -> Path | None:
    """Rotate *path* to a timestamped ``.bak`` file, keeping the last *max_backups*.

    If *path* exists, it is copied to ``<name>.<timestamp>.bak``. If a backup
    with that timestamp already exists (e.g. two saves in the same second), a
    numeric suffix is appended. Older ``.bak`` files (sorted by name, which
    sorts chronologically due to the timestamp) are deleted until only
    *max_backups* remain. Returns the new backup path, or ``None`` if *path*
    did not exist.

    Backups with a ``.comments.bak`` suffix (created by
    :func:`write_opencode_config` when the existing file has JSONC comments)
    are excluded from rotation — those preserve user comments that can't be
    reconstructed from parsed JSON, so they're kept indefinitely.
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
    # Sort by name (timestamp suffix keeps them chronological). Exclude
    # .comments.bak files — those are preserved indefinitely.
    backups = sorted(
        b for b in path.parent.glob(f"{path.name}.*.bak")
        if ".comments.bak" not in b.name
    )
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