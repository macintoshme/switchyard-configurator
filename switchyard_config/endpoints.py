"""Endpoint probing and switchyard runtime status."""

from __future__ import annotations

import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from .constants import SWITCHYARD_URL


def fetch_models_from_endpoint(
    endpoint: str, token: str, timeout: float = 15.0
) -> list[str]:
    """Fetch model list from an OpenAI-compatible /models endpoint.

    When *token* is empty, no Authorization header is sent — for providers
    that don't require auth (e.g., local vLLM, Ollama). *timeout* is the
    per-request timeout in seconds; the default is generous for refreshing
    known providers, but :func:`probe_endpoint` passes a shorter value
    when scanning many candidate URLs.
    """
    url = endpoint.rstrip("/") + "/models"
    headers: dict[str, str] = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    models = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(models, list):
        raise ValueError(f"Unexpected response: {str(data)[:200]}")
    return sorted(m.get("id", "?") for m in models if isinstance(m, dict))


# Common ports for LLM servers (ordered by likelihood for auto-probing)
COMMON_LLM_PORTS: list[int] = [
    11434,  # Ollama
    1234,   # LM Studio
    8000,   # vLLM / common API
    8080,   # common API
    5000,   # common (Flask, etc.)
    3000,   # common
    8888,   # sometimes LLM
]


def generate_endpoint_candidates(host: str) -> list[str]:
    """Generate candidate endpoint URLs to probe for a user-entered host.

    Handles three input forms:
      - Full URL (has ``://``): use as-is, plus try appending ``/v1``
      - ``host:port``: try https:// and http://, with and without ``/v1``
      - Bare ``host``: try default ports + common LLM ports, http/https, with
        and without ``/v1``
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        if url not in seen:
            seen.add(url)
            candidates.append(url)

    if "://" in host:
        base = host.rstrip("/")
        add(base)
        if not base.endswith("/v1"):
            add(base + "/v1")
    else:
        clean = host.strip().strip("/")
        host_part = clean.split("/")[0]
        if ":" in host_part:
            for scheme in ("https", "http"):
                add(f"{scheme}://{host_part}")
                add(f"{scheme}://{host_part}/v1")
        else:
            for scheme in ("https", "http"):
                add(f"{scheme}://{host_part}")
                add(f"{scheme}://{host_part}/v1")
            for port in COMMON_LLM_PORTS:
                for scheme in ("http", "https"):
                    add(f"{scheme}://{host_part}:{port}")
                    add(f"{scheme}://{host_part}:{port}/v1")

    return candidates


def probe_endpoint(host: str, api_key: str = "") -> tuple[str, list[str]]:
    """Try to find a working ``/models`` endpoint for *host*.

    Generates candidate URLs (see :func:`generate_endpoint_candidates`) and
    probes them in parallel. Returns ``(working_url, models)`` on the first
    success, or raises ``ConnectionError`` if none work. Each candidate uses
    a short timeout (5s) since these are typically local/fast endpoints;
    parallel probing keeps the worst case at ~5s instead of 8 minutes.
    """
    candidates = generate_endpoint_candidates(host)
    if not candidates:
        raise ConnectionError(f"No candidate URLs to probe for '{host}'")

    probe_timeout = 5.0
    # Cap workers to avoid spawning a thread per candidate when the list is
    # large — 8 is plenty for the ~32-URL worst case (bare hostname).
    max_workers = min(8, len(candidates))

    def try_one(url: str) -> tuple[str, list[str]]:
        return url, fetch_models_from_endpoint(url, api_key, timeout=probe_timeout)

    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_url: dict = {
            ex.submit(try_one, url): url for url in candidates
        }
        pending: set = set(future_to_url)
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                url = future_to_url[future]
                try:
                    result_url, models = future.result()
                    # First success — cancel any pending futures and return.
                    for f in pending:
                        f.cancel()
                    return result_url, models
                except Exception as e:
                    errors.append(f"  {url}: {type(e).__name__}: {e}")

    tried_list = "\n".join(errors)
    raise ConnectionError(
        f"Could not reach '{host}'. Tried {len(candidates)} URL(s):\n{tried_list}"
    )


def is_switchyard_running() -> bool:
    """Check if the switchyard server answers its /health endpoint.

    In Kubernetes the configurator reaches switchyard via its Service
    (``SWITCHYARD_URL``); locally it defaults to localhost:4000.
    """
    try:
        with urllib.request.urlopen(f"{SWITCHYARD_URL}/health", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def restart_switchyard() -> tuple[bool, str]:
    """Restart of the switchyard server — not managed by the configurator.

    Rollout is handled by the platform: in Kubernetes the Stakater Reloader
    annotation on the switchyard Deployment restarts the pod whenever the
    config ConfigMap changes (i.e. on every save). The endpoint is kept so
    the UI can surface this to the user.
    """
    return (
        False,
        "Restarts are handled automatically: saving updates the ConfigMap and "
        "Stakater Reloader rolls the switchyard Deployment. "
        "If Reloader is not installed, run: "
        "kubectl rollout restart deployment/<switchyard>",
    )