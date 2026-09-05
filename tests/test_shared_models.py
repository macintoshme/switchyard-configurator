"""Two providers serving the same model id.

Each provider's copy of a shared model must be its own, individually
selectable route target (a load balancer / random route can then split
traffic across providers), and the config must round-trip without
collapsing them back into one target.
"""

import pytest
from switchyard_config.models import (
    ConfigState,
    Provider,
    Route,
    parse_ref,
    qualify_ref,
)
from switchyard_config.routes import generate_toml, parse_routes_text, ensure_passthrough_routes

from configurator import state as state_mod


def _shared_state():
    pa = Provider(name="prod", display_name="Prod", endpoint="http://a/v1")
    pa.selected_models = ["GLM-5.2", "OnlyA"]
    pb = Provider(name="staging", display_name="Staging", endpoint="http://b/v1")
    pb.selected_models = ["GLM-5.2", "OnlyB"]
    return pa, pb


def test_generate_toml_emits_one_target_per_provider():
    pa, pb = _shared_state()
    s = ConfigState(
        providers=[pa, pb],
        routes=[
            Route(name="lb", id="switchyard/lb", type="random",
                  targets=[qualify_ref("prod", "GLM-5.2"),
                           qualify_ref("staging", "GLM-5.2")]),
        ],
    )
    text = generate_toml(s)
    # Two targets for the shared model, each pinned to a different provider.
    assert text.count("GLM-5.2") >= 2
    assert 'id = "GLM-5.2"' in text
    # Each target table carries its own llm_client.
    lines = [l.strip() for l in text.splitlines()]
    ids = [i for i, l in enumerate(lines) if l == 'id = "GLM-5.2"']
    clients = []
    for pos in ids:
        for l in lines[pos + 1:]:
            if l.startswith("llm_client"):
                clients.append(l)
                break
    assert clients == ['llm_client = "prod"', 'llm_client = "staging"']
    # The random route references both targets.
    assert "switchyard/lb" in text


def test_roundtrip_keeps_both_providers():
    pa, pb = _shared_state()
    route = Route(name="lb", id="switchyard/lb", type="random",
                  targets=[qualify_ref("prod", "GLM-5.2"),
                           qualify_ref("staging", "GLM-5.2")])
    s = ConfigState(providers=[pa, pb], routes=[route])
    text = generate_toml(s)
    routes, providers, extras, err = parse_routes_text(text)
    assert err is None
    out = {r.name: r for r in routes}["lb"]
    assert out.targets == [qualify_ref("prod", "GLM-5.2"),
                           qualify_ref("staging", "GLM-5.2")]
    # model_extras keys are provider-qualified for shared models.
    assert extras.get(qualify_ref("prod", "GLM-5.2")) is None


def test_target_options_exposes_both_copies():
    pa, pb = _shared_state()
    s = ConfigState(providers=[pa, pb], routes=[])
    opts = {o["ref"]: o for o in s.target_options()}
    assert opts[qualify_ref("prod", "GLM-5.2")]["ambiguous"] is True
    assert opts[qualify_ref("staging", "GLM-5.2")]["ambiguous"] is True
    assert opts[qualify_ref("prod", "GLM-5.2")]["model"] == "GLM-5.2"
    # Unique models keep bare refs.
    assert opts["OnlyA"]["ambiguous"] is False
    assert opts["OnlyB"]["ambiguous"] is False


def test_ambiguous_models_get_no_auto_passthrough():
    pa, pb = _shared_state()
    s = ConfigState(providers=[pa, pb], routes=[])
    new = ensure_passthrough_routes([], s.target_options())
    ids = {r.id for r in new}
    # Shared GLM-5.2 needs an explicit (qualified) route, not a bare passthrough.
    assert "GLM-5.2" not in ids
    assert "OnlyA" in ids and "OnlyB" in ids


def test_parse_ref():
    assert parse_ref(qualify_ref("prod", "GLM-5.2")) == ("prod", "GLM-5.2")
    assert parse_ref("GLM-5.2") is None
    assert parse_ref("prod|") is None
    assert parse_ref("|GLM") is None


# ---------------------------------------------------------------------------
# Manager-level integration (snapshot targets + models list)
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


def test_snapshot_targets(manager):
    pa, pb = _shared_state()
    manager.state = ConfigState(providers=[pa, pb], routes=[])
    snap = manager.snapshot()
    refs = [t["ref"] for t in snap["targets"]]
    assert qualify_ref("prod", "GLM-5.2") in refs
    assert qualify_ref("staging", "GLM-5.2") in refs
    assert "OnlyA" in refs and "OnlyB" in refs
    # Models tab lists one row per provider for the shared model.
    rows = [(m["provider"], m["model"]) for m in snap["models"]]
    assert ("prod", "GLM-5.2") in rows
    assert ("staging", "GLM-5.2") in rows


def test_set_model_extras_per_provider(manager):
    pa, pb = _shared_state()
    manager.state = ConfigState(providers=[pa, pb], routes=[])
    r = manager.set_model_extras(
        qualify_ref("prod", "GLM-5.2"), {"supports_images": True}
    )
    assert r["ok"], r.get("error")
    r = manager.set_model_extras(
        qualify_ref("staging", "GLM-5.2"), {"supports_images": False}
    )
    assert r["ok"], r.get("error")
    assert manager.state.model_extras[qualify_ref("prod", "GLM-5.2")] == {
        "supports_images": True
    }
    assert manager.state.model_extras[qualify_ref("staging", "GLM-5.2")] == {
        "supports_images": False
    }