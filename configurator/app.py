"""FastAPI backend for the Switchyard web configurator.

Serves the static frontend and exposes a small JSON API over the
:class:`configurator.state.ConfigManager`, which reuses the same logic
as the original terminal wizard (``configure.py``).

Behaviors mirror the wizard: probe providers, manage routes, review the
generated files, then save ``routes.toml`` / ``.env`` and optionally
restart the switchyard container.

Run directly:   uv run configurator/app.py
In compose:     uvicorn configurator.app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .state import manager
from switchyard_config import hints as model_hints

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="NeMo Switchyard Configurator",
    description="Web UI for configuring the Switchyard compose stack.",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.middleware("http")
async def nocache_static(request, call_next):
    """Prevent the browser from caching static assets during development."""
    resp = await call_next(request)
    if request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ProbeRequest(BaseModel):
    endpoint: str = ""
    api_key: str = ""


class ProviderPayload(BaseModel):
    provider: dict


class RoutePayload(BaseModel):
    route: dict


class ModelExtrasPayload(BaseModel):
    model: str
    extra_body: dict


# ---------------------------------------------------------------------------
# Health / status
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/status")
def status() -> dict:
    return manager.status()


@app.get("/api/debug/cycles")
def debug_cycles() -> dict:
    # Expose any route dependency cycles for quick diagnostics.
    return {"cycles": manager.cycles()}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@app.get("/api/state")
def get_state() -> dict:
    return manager.snapshot()


@app.post("/api/providers/refresh")
def refresh_models() -> dict:
    return manager.refresh_models()


@app.get("/api/preview")
def get_preview() -> dict:
    return manager.preview()


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

@app.post("/api/providers")
def add_provider(payload: ProviderPayload) -> dict:
    return manager.upsert_provider(-1, payload.provider)


@app.put("/api/providers/{index}")
def update_provider(index: int, payload: ProviderPayload) -> dict:
    return manager.upsert_provider(index, payload.provider)


@app.delete("/api/providers/{index}")
def delete_provider(index: int) -> dict:
    return manager.delete_provider(index)


@app.post("/api/providers/probe")
def probe(payload: ProbeRequest) -> dict:
    return manager.probe(payload.endpoint, payload.api_key)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/routes")
def add_route(payload: RoutePayload) -> dict:
    return manager.upsert_route(-1, payload.route)


@app.put("/api/routes/{index}")
def update_route(index: int, payload: RoutePayload) -> dict:
    return manager.upsert_route(index, payload.route)


@app.delete("/api/routes/{index}")
def delete_route(index: int) -> dict:
    return manager.delete_route(index)


@app.post("/api/sync")
def sync() -> dict:
    return manager.resync()


# ---------------------------------------------------------------------------
# Models (per-model extra_body / capability settings)
# ---------------------------------------------------------------------------

@app.get("/api/model-hints")
def get_model_hints() -> dict:
    return model_hints.load_model_hints()


@app.post("/api/models/extras")
def set_model_extras(payload: ModelExtrasPayload) -> dict:
    return manager.set_model_extras(payload.model, payload.extra_body)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

@app.post("/api/save")
def save() -> dict:
    return manager.save()


@app.post("/api/discard")
def discard() -> dict:
    return manager.discard_draft()


@app.post("/api/restart")
def restart() -> dict:
    return manager.restart()


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("CONFIGURATOR_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
