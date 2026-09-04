"""Provider token persistence: the UI-managed token Secret (kube.py) and
its wiring into the Kubernetes save path (ConfigManager)."""

import pytest
from kubernetes.client import ApiException

from configurator import state as state_mod
from switchyard_config import kube
from switchyard_config.models import ConfigState, Provider, Route


class _FakeApi:
    def __init__(self, patch_fails_with=None, create_fails_with=None):
        self.patch_fails_with = patch_fails_with
        self.create_fails_with = create_fails_with
        self.patched = []
        self.created = []

    def patch_namespaced_secret(self, name, ns, body):
        if self.patch_fails_with:
            raise ApiException(status=self.patch_fails_with)
        self.patched.append((name, ns, body))

    def create_namespaced_secret(self, ns, body):
        if self.create_fails_with:
            raise ApiException(status=self.create_fails_with)
        self.created.append((ns, body))


@pytest.fixture
def fake_api(monkeypatch):
    def install(api):
        monkeypatch.setattr(kube, "_core_api", lambda: api)
        monkeypatch.setattr(kube, "namespace", lambda: "test-ns")
        monkeypatch.setattr(kube, "token_secret_name", lambda: "sy-tokens")
        return api

    return install


def test_upsert_patches_existing_secret(fake_api):
    api = fake_api(_FakeApi())
    kube.upsert_token_secret({"OPENAI_API_KEY": "sk-1"})
    assert len(api.patched) == 1
    name, ns, body = api.patched[0]
    assert (name, ns) == ("sy-tokens", "test-ns")
    assert body["stringData"] == {"OPENAI_API_KEY": "sk-1"}
    assert api.created == []


def test_upsert_creates_when_missing(fake_api):
    api = fake_api(_FakeApi(patch_fails_with=404))
    kube.upsert_token_secret({"MY_TOKEN": "t"})
    assert api.created and not api.patched
    ns, body = api.created[0]
    assert ns == "test-ns"
    assert body["metadata"]["name"] == "sy-tokens"
    assert body["stringData"] == {"MY_TOKEN": "t"}


def test_upsert_noop_without_name_or_tokens(fake_api, monkeypatch):
    api = fake_api(_FakeApi())
    monkeypatch.setattr(kube, "token_secret_name", lambda: "")
    kube.upsert_token_secret({"A": "b"})
    kube.upsert_token_secret({})
    assert api.patched == [] and api.created == []


def test_upsert_surfaces_api_errors(fake_api):
    fake_api(_FakeApi(patch_fails_with=403))
    with pytest.raises(RuntimeError, match="token Secret"):
        kube.upsert_token_secret({"A": "b"})
    fake_api(_FakeApi(patch_fails_with=404, create_fails_with=409))
    with pytest.raises(RuntimeError, match="token Secret"):
        kube.upsert_token_secret({"A": "b"})


# ---------------------------------------------------------------------------
# Save path: tokens reach the Secret, everything else is skipped
# ---------------------------------------------------------------------------

@pytest.fixture
def k8s_manager(monkeypatch, tmp_path):
    """ConfigManager saving through the Kubernetes path with kube calls
    captured instead of hitting a cluster."""
    calls = {"config": {}, "tokens": {}}

    monkeypatch.setattr(state_mod.kube, "in_cluster", lambda: True)
    monkeypatch.setattr(
        state_mod.kube, "patch_config_data", lambda data: calls.__setitem__("config", data)
    )
    monkeypatch.setattr(
        state_mod.kube, "configmap_name", lambda: "sy-config"
    )
    monkeypatch.setattr(
        state_mod.kube, "upsert_token_secret",
        lambda tokens: calls.__setitem__("tokens", tokens),
    )
    monkeypatch.setattr(
        state_mod.kube, "token_secret_name", lambda: "sy-tokens"
    )
    monkeypatch.setattr(state_mod, "is_switchyard_running", lambda: False)
    monkeypatch.setattr(state_mod.C, "ROUTES_TOML", tmp_path / "routes.toml")
    monkeypatch.setattr(state_mod.C, "ROUTES_DRAFT", tmp_path / "routes.toml.draft")
    monkeypatch.setattr(state_mod.C, "ENV_DRAFT", tmp_path / ".env.draft")
    monkeypatch.setattr(state_mod.C, "PROVIDER_META_FILE", tmp_path / "provider_meta.json")
    monkeypatch.setattr(state_mod.C, "PROVIDER_META_DRAFT", tmp_path / "provider_meta.json.draft")

    m = state_mod.ConfigManager()
    m.load_errors = []
    m.calls = calls
    return m


def _state_with_providers(*specs):
    providers = []
    for name, endpoint, env, key in specs:
        p = Provider(name=name, endpoint=endpoint)
        p.api_key_env = env
        p.api_key = key
        p.selected_models = [f"{name}-model"]
        providers.append(p)
    route = Route(name="r", id=f"{specs[0][0]}-model", type="passthrough", target=f"{specs[0][0]}-model")
    s = ConfigState(providers=providers, routes=[route])
    for p in providers:
        s.selected_models.extend(p.selected_models)
    return s


def test_save_persists_tokens_to_secret(k8s_manager):
    k8s_manager.state = _state_with_providers(
        ("openai", "https://api.openai.com/v1", "OPENAI_API_KEY", "sk-secret"),
        ("vllm", "http://vllm:8000/v1", "", ""),
        ("probe", "http://x/v1", "PROBE_KEY", ""),  # env name but no value
    )
    r = k8s_manager.save()
    assert r["ok"], r.get("error")
    assert k8s_manager.calls["tokens"] == {"OPENAI_API_KEY": "sk-secret"}
    assert any(l.startswith("secret/sy-tokens: OPENAI_API_KEY") for l in r["files"])


def test_save_without_tokens_skips_secret(k8s_manager):
    k8s_manager.state = _state_with_providers(
        ("vllm", "http://vllm:8000/v1", "", ""),
    )
    r = k8s_manager.save()
    assert r["ok"], r.get("error")
    assert k8s_manager.calls["tokens"] == {}
    assert not any("secret/" in l for l in r["files"])


def test_save_token_failure_surfaces_error(k8s_manager, monkeypatch):
    def boom(tokens):
        raise RuntimeError("token Secret patch failed: nope")

    monkeypatch.setattr(state_mod.kube, "upsert_token_secret", boom)
    k8s_manager.state = _state_with_providers(
        ("openai", "https://api.openai.com/v1", "OPENAI_API_KEY", "sk-secret"),
    )
    r = k8s_manager.save()
    assert not r["ok"]
    assert "token Secret" in r["error"]