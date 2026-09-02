"""Paths and shared constants for the switchyard configurator."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROUTES_TOML = PROJECT_ROOT / "switchyard" / "config" / "routes.toml"
ENV_FILE = PROJECT_ROOT / ".env"
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"

# Draft files capture unsaved edits so they survive a crash or quit-without-save.
# On save, the live config is rotated to a timestamped .bak (keeping MAX_BACKUPS)
# and the draft files are cleaned up.
ROUTES_DRAFT = ROUTES_TOML.parent / (ROUTES_TOML.name + ".draft")
ENV_DRAFT = ENV_FILE.parent / (ENV_FILE.name + ".draft")
MAX_BACKUPS = 5

# Sidecar file for provider metadata (display names) that doesn't fit in
# routes.toml's llm_clients table (which rejects unknown fields).
PROVIDER_META_FILE = ROUTES_TOML.parent / "provider_meta.json"
PROVIDER_META_DRAFT = PROVIDER_META_FILE.parent / (PROVIDER_META_FILE.name + ".draft")

# opencode config locations (project-local preferred for writes; global as
# fallback when the user already has one there).
OPENCODE_PROJECT_CONFIG = PROJECT_ROOT / "opencode.json"
OPENCODE_GLOBAL_DIR = Path.home() / ".config" / "opencode"
SWITCHYARD_DEFAULT_PORT = "4000"
SWITCHYARD_PROVIDER_ID = "switchyard"
SELF_PROVIDER_NAME = "self"

# OpenRouter public models endpoint — used to look up per-model pricing
# (per-token USD strings) so we can attach a `cost` block to each switchyard
# passthrough model in the opencode config.
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_TIMEOUT = 10.0

TOTAL_STEPS = 4  # 0=welcome, 1=providers, 2=routes, 3=review