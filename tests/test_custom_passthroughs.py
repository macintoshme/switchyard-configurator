"""User-created passthrough routes must stay visible in the web UI.

The UI hides only the auto-generated passthroughs (one per selected
model, named ``passthrough_<model id>`` with id == target == model id).
A passthrough the user created by hand is a custom route and must be
listed like any other route — see is_auto_passthrough and
ConfigManager.snapshot.
"""

import pytest
from switchyard_config.models import ConfigState, Provider, Route
from switchyard_config.routes import (
    auto_passthrough_name,
    ensure_passthrough_routes,
    is_auto_passthrough,
)

from configurator import state as state_mod


def _auto(model: str) -> Route:
    return Route(
        name=auto_passthrough_name(model),
        id=model,
        type="passthrough",
        target=model,
    )


# ---------------------------------------------------------------------------
# is_auto_passthrough
# ---------------------------------------------------------------------------

def test_is_auto_passthrough_generated_routes():
    routes = ensure_passthrough_routes([], ["GLM-5.2", "meta-llama/Llama-3.1-8B"])
    assert len(routes) == 2
    for r in routes:
        assert is_auto_passthrough(r)


def test_is_auto_passthrough_custom_name_is_custom():
    # Same id/target as the auto route would have, but a user-chosen name.
    r = Route(name="glm_direct", id="GLM-5.2", type="passthrough", target="GLM-5.2")
    assert not is_auto_passthrough(r)


def test_is_auto_passthrough_synthetic_route_id_is_custom():
    # Passthrough chaining through switchyard: id is a route id, not a
    # model id — even with an auto-style name, target != id.
    r = Route(
        name="passthrough_switchyard_good",
        id="switchyard/good",
        type="passthrough",
        target="GLM-5.2",
    )
    assert not is_auto_passthrough(r)


def test_is_auto_passthrough_other_types():
    assert not is_auto_passthrough(
        Route(name="r", id="A", type="random", targets=["A", "B"])
    )
    assert not is_auto_passthrough(
        Route(name="r", id="", type="passthrough", target="A")
    )


# ---------------------------------------------------------------------------
# snapshot: custom vs auto counts
# ---------------------------------------------------------------------------

@pytest.fixture
def manager(monkeypatch, tmp_path):
    monkeypatch.setattr(state_mod, "is_switchyard_running", lambda: False)
    monkeypatch.setattr(state_mod.C, "ROUTES_TOML", tmp_path / "routes.toml")
    monkeypatch.setattr(state_mod.C, "ROUTES_DRAFT", tmp_path / "routes.toml.draft")
    monkeypatch.setattr(state_mod.C, "ENV_DRAFT", tmp_path / ".env.draft")
    monkeypatch.setattr(state_mod.C, "PROVIDER_META_FILE", tmp_path / "provider_meta.json")
    monkeypatch.setattr(state_mod.C, "PROVIDER_META_DRAFT", tmp_path / "provider_meta.json.draft")
    m = state_mod.ConfigManager()
    m.load_errors = []
    return m


def _names(manager, snap):
    return [manager.state.routes[i].name for i in snap["custom_route_indices"]]


def test_snapshot_lists_custom_passthroughs(manager):
    prov = Provider(name="vllm", endpoint="http://example/v1")
    prov.selected_models = ["GLM-5.2", "Qwen3.8-27B-FP8"]
    chained = Route(
        name="chained", id="switchyard/good",
        type="passthrough", target="GLM-5.2",
    )
    random = Route(
        name="ab", id="switchyard/ab", type="random",
        targets=["GLM-5.2", "Qwen3.8-27B-FP8"],
    )
    manager.state = ConfigState(
        providers=[prov],
        routes=[chained, random, _auto("GLM-5.2"), _auto("Qwen3.8-27B-FP8")],
    )
    snap = manager.snapshot()
    assert _names(manager, snap) == ["chained", "ab"]
    assert snap["passthrough_count"] == 2


def test_snapshot_custom_passthrough_covering_model_id(manager):
    # The user's passthrough claims the model id, so no auto route is
    # generated for it — the user's route must still be shown.
    prov = Provider(name="vllm", endpoint="http://example/v1")
    prov.selected_models = ["GLM-5.2"]
    custom = Route(name="glm_direct", id="GLM-5.2", type="passthrough", target="GLM-5.2")
    manager.state = ConfigState(providers=[prov], routes=[custom])
    snap = manager.snapshot()
    assert snap["custom_route_indices"] == [0]
    assert snap["passthrough_count"] == 0


def test_upsert_custom_passthrough_visible(manager):
    # UI-created passthroughs use a route id distinct from the target
    # (id == target is rejected as a self-reference), e.g. a
    # switchyard-served alias for one model.
    prov = Provider(name="vllm", endpoint="http://example/v1")
    prov.selected_models = ["GLM-5.2"]
    manager.state = ConfigState(providers=[prov], routes=[_auto("GLM-5.2")])
    r = manager.upsert_route(
        -1, {"name": "glm_direct", "id": "switchyard/glm",
             "type": "passthrough", "target": "GLM-5.2"}
    )
    assert r["ok"], r.get("error")
    snap = r["state"]
    assert _names(manager, snap) == ["glm_direct"]
    # The per-model auto passthrough is still hidden.
    assert snap["passthrough_count"] == 1
    assert len(manager.state.routes) == 2
