"""Paths and shared constants for the switchyard configurator."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROUTES_TOML = PROJECT_ROOT / "switchyard" / "config" / "routes.toml"
ENV_FILE = PROJECT_ROOT / ".env"

# Draft files capture unsaved edits so they survive a crash or quit-without-save.
# On save, the live config is rotated to a timestamped .bak (keeping MAX_BACKUPS)
# and the draft files are cleaned up.
#
# In Kubernetes the final config lives in a ConfigMap (see kube.py); drafts go
# to a dedicated writable directory set via CONFIGURATOR_DRAFT_DIR (e.g. an
# emptyDir volume). Locally, drafts default to the project root.
DRAFT_DIR = Path(os.environ.get("CONFIGURATOR_DRAFT_DIR", str(PROJECT_ROOT)))
ROUTES_DRAFT = DRAFT_DIR / "routes.toml.draft"
ENV_DRAFT = DRAFT_DIR / ".env.draft"
MAX_BACKUPS = 5

# Sidecar file for provider metadata (display names) that doesn't fit in
# routes.toml's llm_clients table (which rejects unknown fields).
PROVIDER_META_FILE = ROUTES_TOML.parent / "provider_meta.json"
PROVIDER_META_DRAFT = DRAFT_DIR / "provider_meta.json.draft"

# Base URL of the running switchyard server (Kubernetes: the Service DNS name;
# locally: localhost). Used for status checks; restarts are not managed here.
SWITCHYARD_URL = os.environ.get("SWITCHYARD_URL", "http://localhost:4000")

SWITCHYARD_DEFAULT_PORT = "4000"
SELF_PROVIDER_NAME = "self"

TOTAL_STEPS = 4  # 0=welcome, 1=providers, 2=routes, 3=review