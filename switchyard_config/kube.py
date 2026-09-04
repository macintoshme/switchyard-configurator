"""Kubernetes adapter for the configurator (in-cluster mode only).

When the configurator runs inside Kubernetes (the in-cluster service-account
config loads), the ConfigMap named by ``CONFIGMAP_NAME`` is the source of
truth for ``routes.toml`` and ``provider_meta.json`` instead of local files.

A second, code-maintained ConfigMap (``SEED_CONFIGMAP_NAME``, rendered by
the Helm chart from ``values.routesToml``) can override it: whenever the
seed's content changed since the runtime ConfigMap last synced from it,
the configurator applies the seed at boot ("maintained in code" wins;
otherwise UI-saved edits win).

All ``kubernetes`` imports are deferred to call time so this module can be
imported (and unit-tested) in environments without the client library
installed, e.g. local development and CI.
"""

from __future__ import annotations

import hashlib
import os

_SA_NAMESPACE_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"

# Runtime ConfigMaps carry the checksum of the seed content they last
# synced from in this annotation.
SEED_CHECKSUM_ANNOTATION = "switchyard.nvidia.com/seed-checksum"

_api = None
_in_cluster: bool | None = None


def in_cluster() -> bool:
    """True when running inside a Kubernetes pod with a service account."""
    global _in_cluster
    if _in_cluster is None:
        _in_cluster = False
        try:
            from kubernetes import config as k8s_config
        except ImportError:
            # Client library not installed (local dev / CI).
            pass
        else:
            try:
                k8s_config.load_incluster_config()
                _in_cluster = True
            except k8s_config.ConfigException:
                # Not running in a pod (no service-account files mounted).
                pass
    return _in_cluster


def namespace() -> str:
    """Namespace of the pod (from the service-account token), for RBAC."""
    try:
        with open(_SA_NAMESPACE_FILE, encoding="utf-8") as f:
            ns = f.read().strip()
        if ns:
            return ns
    except OSError:
        pass
    return os.environ.get("CONFIGURATOR_NAMESPACE", "default")


def configmap_name() -> str:
    """ConfigMap holding routes.toml / provider_meta.json (env-configured)."""
    return os.environ.get("CONFIGMAP_NAME", "")


def _core_api():
    global _api
    if _api is None:
        from kubernetes import client as k8s_client
        from kubernetes import config as k8s_config

        k8s_config.load_incluster_config()
        _api = k8s_client.CoreV1Api()
    return _api


def load_config_data() -> dict[str, str]:
    """Return the ConfigMap's data map (empty when missing/unreadable)."""
    name = configmap_name()
    if not name:
        return {}
    from kubernetes import config as k8s_config
    from kubernetes.client import ApiException

    try:
        cm = _core_api().read_namespaced_config_map(name, namespace())
        return dict(cm.data or {})
    except (ApiException, k8s_config.ConfigException):
        # Missing ConfigMap, RBAC denial, or broken in-cluster config.
        return {}


def patch_config_data(data: dict[str, str]) -> None:
    """Merge *data* into the ConfigMap (strategic merge patch on ``data``).

    Raises ``RuntimeError`` on API errors so callers can surface the failure
    without importing the kubernetes client themselves.
    """
    name = configmap_name()
    if not name:
        raise RuntimeError("CONFIGMAP_NAME is not set")
    from kubernetes.client import ApiException

    try:
        _core_api().patch_namespaced_config_map(name, namespace(), body={"data": data})
    except ApiException as e:
        raise RuntimeError(f"ConfigMap patch failed: {e}") from e


def seed_configmap_name() -> str:
    """Code-maintained ConfigMap the runtime config follows (env-configured)."""
    return os.environ.get("SEED_CONFIGMAP_NAME", "")


def token_secret_name() -> str:
    """UI-managed Secret holding provider tokens (env-configured).

    Empty when the feature is disabled (chart older than the token secret
    or ``configurator.tokenSecret.enabled: false``); the save path then
    keeps the legacy behaviour of not persisting tokens.
    """
    return os.environ.get("TOKEN_SECRET_NAME", "")


def upsert_token_secret(tokens: dict[str, str]) -> None:
    """Create the token Secret or merge *tokens* into it (env var -> value).

    Merge-only: keys the configurator does not know (providers whose token
    was never entered in the UI, keys pre-provisioned out of band) are left
    untouched, and keys are never removed — deleting a provider in the UI
    leaves its token behind (harmless; clean up manually if desired).

    The Secret is created by the configurator's API client and never
    rendered by the chart, so Helm does not own it and upgrades cannot
    conflict. Raises ``RuntimeError`` on API errors so ``save()`` surfaces
    a failed token persist instead of silently dropping it.
    """
    name = token_secret_name()
    if not name or not tokens:
        return
    from kubernetes.client import ApiException

    api = _core_api()
    ns = namespace()
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {
            "name": name,
            # Discoverability only — no Helm-owned labels (see
            # _stable_labels) so nothing conflicts on future upgrades.
            "labels": {
                "app.kubernetes.io/name": "switchyard",
                "app.kubernetes.io/component": "tokens",
                "app.kubernetes.io/managed-by": "switchyard-configurator",
            },
        },
        # stringData: the API server base64-encodes into .data on write;
        # strategic merge patch adds/updates keys without touching others.
        "stringData": dict(tokens),
    }
    try:
        api.patch_namespaced_secret(name, ns, body=body)
    except ApiException as e:
        if e.status != 404:
            raise RuntimeError(f"token Secret patch failed: {e}") from e
        try:
            api.create_namespaced_secret(ns, body=body)
        except ApiException as e:
            raise RuntimeError(f"token Secret create failed: {e}") from e


def plan_seed_sync(seed_data: dict[str, str] | None, runtime: dict | None) -> dict:
    """Decide how the runtime ConfigMap should follow the seed ConfigMap.

    Pure decision table so the sync policy is unit-testable without a
    cluster. *seed_data* is the seed ConfigMap's ``data`` map (``None`` or
    empty when the seed is not deployed); *runtime* is the runtime ConfigMap
    as a plain dict (``to_dict()`` shape, ``None`` when it does not exist).

    Returns ``{"action": "none"}`` or a plan with ``checksum`` and, for
    "create"/"sync", the ``data`` to write. Actions:

    - none:   seed absent, or the runtime CM already matches it
    - adopt:  runtime CM predates the seed feature (no checksum recorded):
              record the checksum but keep the current content
    - sync:   seed changed in code since the last sync (or the runtime CM
              has no routes.toml yet): write the seed content
    - create: runtime CM is gone entirely: recreate it from the seed
    """
    if not seed_data or "routes.toml" not in seed_data:
        return {"action": "none"}
    seed_routes = seed_data["routes.toml"]
    checksum = hashlib.sha256(seed_routes.encode("utf-8")).hexdigest()

    data: dict[str, str | None] = {"routes.toml": seed_routes}
    if "provider_meta.json" in seed_data:
        data["provider_meta.json"] = seed_data["provider_meta.json"]

    if runtime is None:
        return {"action": "create", "checksum": checksum, "data": dict(data)}

    runtime_data = runtime.get("data") or {}
    annotations = (runtime.get("metadata") or {}).get("annotations") or {}
    tracked = annotations.get(SEED_CHECKSUM_ANNOTATION)

    if "routes.toml" not in runtime_data:
        # Empty shell (no config yet): fill it from the seed.
        return {"action": "sync", "checksum": checksum, "data": dict(data)}

    if tracked is None:
        # Legacy CM from before the seed feature: never clobber existing
        # content on first contact, just record the current seed.
        return {"action": "adopt", "checksum": checksum}

    if tracked != checksum:
        # The seed changed in code since this CM last synced: code wins.
        if "provider_meta.json" not in seed_data:
            # Drop display-name metadata belonging to the replaced content.
            data["provider_meta.json"] = None
        return {"action": "sync", "checksum": checksum, "data": data}

    return {"action": "none"}


def _stable_labels(labels: object) -> dict[str, str]:
    """Labels the configurator may own when recreating the runtime ConfigMap.

    Version-coupled labels (``helm.sh/chart``, ``app.kubernetes.io/version``)
    belong to Helm's field manager — creating the ConfigMap with them would
    make our API client their owner, so the next chart upgrade (which
    changes those values) hits a server-side-apply conflict. Everything
    else (name, instance, component, managed-by, ...) is stable across
    upgrades and safe to copy for discoverability.
    """
    if not isinstance(labels, dict):
        return {}
    return {
        k: str(v)
        for k, v in labels.items()
        if k not in ("helm.sh/chart", "app.kubernetes.io/version")
    }


def sync_seed() -> str:
    """Reconcile the runtime ConfigMap from the seed ConfigMap (boot time).

    Applies the policy from ``plan_seed_sync``: the seed only wins when its
    content changed since the runtime ConfigMap last synced from it. Returns
    a human-readable note for the log (empty when nothing happened); raises
    ``RuntimeError`` on API failures so callers can surface them.
    """
    seed_name = seed_configmap_name()
    runtime_name = configmap_name()
    if not seed_name or not runtime_name:
        return ""
    from kubernetes.client import ApiException

    api = _core_api()
    ns = namespace()
    try:
        seed_cm = api.read_namespaced_config_map(seed_name, ns)
    except ApiException:
        # Seed ConfigMap not deployed: the feature is disabled.
        return ""
    seed_data = dict(seed_cm.data or {})
    try:
        runtime_cm = api.read_namespaced_config_map(runtime_name, ns)
    except ApiException:
        runtime_cm = None

    plan = plan_seed_sync(
        seed_data, runtime_cm.to_dict() if runtime_cm is not None else None
    )
    action = plan.get("action")
    if action == "none":
        return ""
    checksum = plan["checksum"]

    try:
        if action == "create":
            api.create_namespaced_config_map(
                ns,
                body={
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": runtime_name,
                        "labels": _stable_labels(seed_cm.metadata.labels),
                        "annotations": {SEED_CHECKSUM_ANNOTATION: checksum},
                    },
                    "data": {k: v for k, v in plan["data"].items() if v is not None},
                },
            )
            return (
                f"runtime ConfigMap '{runtime_name}' was missing; "
                f"recreated it from the seed '{seed_name}'"
            )
        if action == "sync":
            # A None value removes the key (stale provider_meta.json).
            api.patch_namespaced_config_map(
                runtime_name, ns, body={"data": plan["data"]}
            )
        api.patch_namespaced_config_map(
            runtime_name,
            ns,
            body={"metadata": {"annotations": {SEED_CHECKSUM_ANNOTATION: checksum}}},
        )
    except ApiException as e:
        raise RuntimeError(f"seed ConfigMap sync failed: {e}") from e

    if action == "adopt":
        return f"seed '{seed_name}' adopted without overwriting (no prior sync recorded)"
    return f"seed '{seed_name}' changed in code; applied it to '{runtime_name}'"