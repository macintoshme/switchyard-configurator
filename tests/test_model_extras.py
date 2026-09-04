"""Models tab: per-model extra_body (capabilities + request params) and
the model hints definitions file that feeds the suggestion badges."""

import json

import pytest
from switchyard_config import hints as hints_mod
from switchyard_config.models import ConfigState, Provider, Route
from switchyard_config.routes import generate_toml, parse_routes_text

from configurator import state as state_mod


def _state_with_model(model="Qwen3.8-27B-FP8", extra=None):
    prov = Provider(name="vllm", endpoint="http://example/v1")
    prov.selected_models = [model]
    route = Route(name="m", id=model, type="passthrough", target=model)
    s = ConfigState(providers=[prov], routes=[route])
    if extra is not None:
        s.model_extras[model] = extra
    return s


# ---------------------------------------------------------------------------
# TOML round-trip
# ---------------------------------------------------------------------------

def test_extra_body_roundtrip():
    s = _state_with_model(extra={"supports_images": True, "top_k": 40,
                                 "label": "x", "nested": {"a": [1, 2]}})
    text = generate_toml(s)
    assert "extra_body" in text
    routes, providers, extras, err = parse_routes_text(text)
    assert err is None
    assert extras["Qwen3.8-27B-FP8"] == {
        "supports_images": True, "top_k": 40, "label": "x",
        "nested": {"a": [1, 2]},
    }
    # Types survive the round-trip (bool stays bool, int stays int).
    assert extras["Qwen3.8-27B-FP8"]["supports_images"] is True
    assert extras["Qwen3.8-27B-FP8"]["top_k"] == 40


def test_extra_body_omitted_when_empty():
    s = _state_with_model()
    text = generate_toml(s)
    assert "extra_body" not in text
    routes, providers, extras, err = parse_routes_text(text)
    assert extras == {}


def test_parse_compose_style_inline_extra_body():
    """The hand-edited compose config format: inline extra_body table."""
    text = """
schema_version = 1

[llm_clients.vllm]
format = "openai_chat"
base_url = "http://192.168.42.155:8000/v1"
max_retries = 2

[targets.target_0]
id = "Qwen3.8-27B-FP8"
llm_client = "vllm"
extra_body = { supports_images = true }

[routes.default]
id = "Qwen3.8-27B-FP8"
type = "passthrough"
target = "target_0"
"""
    routes, providers, extras, err = parse_routes_text(text)
    assert err is None
    assert extras == {"Qwen3.8-27B-FP8": {"supports_images": True}}
    assert providers[0].selected_models == ["Qwen3.8-27B-FP8"]


def test_extra_body_only_for_emitted_targets():
    """Extras for deselected models are not written to routes.toml."""
    s = _state_with_model()
    s.model_extras["not-selected-model"] = {"top_k": 1}
    text = generate_toml(s)
    assert "not-selected-model" not in text
    assert "top_k" not in text


# ---------------------------------------------------------------------------
# Manager: set_model_extras + snapshot models list
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
    m.state = _state_with_model()
    m.load_errors = []
    return m


def test_set_model_extras_and_clear(manager):
    r = manager.set_model_extras("Qwen3.8-27B-FP8", {"supports_images": True})
    assert r["ok"], r.get("error")
    assert manager.state.model_extras["Qwen3.8-27B-FP8"] == {"supports_images": True}

    # Snapshot exposes it on the models list.
    snap = manager.snapshot()
    row = next(m for m in snap["models"] if m["model"] == "Qwen3.8-27B-FP8")
    assert row["extra_body"] == {"supports_images": True}
    assert row["provider"] == "vllm"

    # Empty dict clears the entry.
    r = manager.set_model_extras("Qwen3.8-27B-FP8", {})
    assert r["ok"]
    assert "Qwen3.8-27B-FP8" not in manager.state.model_extras


def test_set_model_extras_validation(manager):
    m = "Qwen3.8-27B-FP8"
    assert not manager.set_model_extras("", {})["ok"]
    assert not manager.set_model_extras("unknown-model", {"a": 1})["ok"]
    assert not manager.set_model_extras(m, {"bad key!": 1})["ok"]
    assert not manager.set_model_extras(m, {"a": None})["ok"]
    assert not manager.set_model_extras(m, {"a": set()})["ok"]
    assert not manager.set_model_extras(m, "nope")["ok"]
    assert not manager.set_model_extras(m, {k: 1 for k in [f"k{i}" for i in range(25)]})["ok"]
    assert not manager.set_model_extras(m, {"a": 'quote"inside'})["ok"]
    # Nested structures are fine (json-typed rows).
    assert manager.set_model_extras(m, {"cfg": {"deep": [1, True, "s"]}})["ok"]


def test_models_summary_skips_self_provider(manager):
    self_prov = Provider(name="self", endpoint="http://localhost:4000/v1")
    self_prov.selected_models = ["switchyard/smart"]
    manager.state.providers.append(self_prov)
    snap = manager.snapshot()
    names = [m["model"] for m in snap["models"]]
    assert "Qwen3.8-27B-FP8" in names
    assert "switchyard/smart" not in names


def test_models_summary_attaches_matched_hint(manager):
    """The backend matches hints (Python regexes the browser can't run)
    and attaches them to the snapshot rows for the Models tab."""
    snap = manager.snapshot()
    row = next(m for m in snap["models"] if m["model"] == "Qwen3.8-27B-FP8")
    assert row["hint"], "baked hints should match Qwen3.8-27B-FP8"
    assert row["hint"]["suggest"]["supports_images"] is True


def test_preview_response_is_ok_and_carries_extras(manager):
    """/api/preview must include ok: true — the frontend gates the
    "Show generated file" flow on it — and the generated TOML must
    carry the model's extra_body."""
    assert manager.set_model_extras(
        "Qwen3.8-27B-FP8", {"supports_images": True}
    )["ok"]
    p = manager.preview()
    assert p["ok"] is True
    assert "extra_body" in p["toml"]
    assert "supports_images = true" in p["toml"]


# ---------------------------------------------------------------------------
# Hints definitions
# ---------------------------------------------------------------------------

def test_load_baked_hints():
    """The definitions shipped with the configurator parse cleanly."""
    loaded = hints_mod.load_model_hints()
    assert loaded["ok"], loaded.get("error")
    assert loaded["hints"], "baked hints file should not be empty"
    for h in loaded["hints"]:
        assert h["pattern"] and isinstance(h["suggest"], dict)


def test_baked_hint_gemma4_google_api():
    """The baked hint matches the Gemini-API model id (models/gemma-4-*)
    and suggests the binary thinking_level control; the local-served id
    must not pick it up."""
    loaded = hints_mod.load_model_hints()
    assert loaded["ok"], loaded.get("error")
    h = hints_mod.match_hint("models/gemma-4-31b-it", loaded["hints"])
    assert h is not None, "Google-served Gemma 4 id should match its hint"
    # Double-wrapped on purpose: the server merges the model's extra_body
    # onto the request top level, while the Gemini endpoint needs the
    # google namespace inside a body-level extra_body envelope.
    assert h["suggest"]["extra_body"]["google"]["thinking_config"][
        "thinking_level"
    ] == "minimal"
    local = hints_mod.match_hint("google/gemma-4-31b", loaded["hints"])
    assert local is None or "thinking_config" not in local["suggest"]


def test_gemma4_hint_suggestion_is_valid_extras():
    """The suggested nested value must be accepted by set_model_extras
    validation, or the UI 'Apply suggestions' would 400."""
    loaded = hints_mod.load_model_hints()
    assert loaded["ok"], loaded.get("error")
    h = hints_mod.match_hint("models/gemma-4-31b-it", loaded["hints"])
    assert h is not None
    from configurator.state import ConfigManager
    for key, value in h["suggest"].items():
        assert ConfigManager._validate_extra_value(value, key) is None


def test_match_hint_first_wins():
    hints = [
        {"pattern": "(?i)qwen3[._-]?8", "description": "specific",
         "suggest": {"supports_images": True}},
        {"pattern": "(?i)vl", "description": "generic",
         "suggest": {"supports_images": True}},
    ]
    h = hints_mod.match_hint("Qwen3.8-27B-FP8", hints)
    assert h["description"] == "specific"
    assert hints_mod.match_hint("Qwen2.5-VL-7B", hints)["description"] == "generic"
    assert hints_mod.match_hint("llama-3-8b", hints) is None


def test_load_hints_missing_file(tmp_path):
    loaded = hints_mod.load_model_hints(str(tmp_path / "nope.toml"))
    assert loaded["ok"]
    assert loaded["hints"] == []


def test_load_hints_skips_bad_entries(tmp_path):
    p = tmp_path / "hints.toml"
    p.write_text(
        "[[hint]]\n"
        "pattern = '(unclosed'\n"
        "description = 'bad'\n"
        "suggest = { a = true }\n"
        "\n"
        "[[hint]]\n"
        "pattern = 'good'\n"
        "description = 'fine'\n"
        "suggest = { b = 2 }\n",
        encoding="utf-8",
    )
    loaded = hints_mod.load_model_hints(str(p))
    assert not loaded["ok"]
    assert loaded["error"]
    assert len(loaded["hints"]) == 1
    assert loaded["hints"][0]["pattern"] == "good"


def test_extras_display():
    assert hints_mod.extras_display(True) == "true"
    assert hints_mod.extras_display(40) == "40"
    assert hints_mod.extras_display("x") == "x"
    assert json.loads(hints_mod.extras_display({"a": [1]})) == {"a": [1]}


def _get_path(obj, path):
    cur = obj
    for seg in path.split("."):
        cur = cur[seg]
    return cur


# ---------------------------------------------------------------------------
# Hint tunables (first-class UI controls for nested provider params)
# ---------------------------------------------------------------------------

def test_load_hints_with_tunables(tmp_path):
    p = tmp_path / "hints.toml"
    p.write_text(
        "[[hint]]\n"
        "pattern = 'models/gemma-4'\n"
        "description = 'd'\n"
        "suggest = { extra_body = { google = { thinking_config = { thinking_level = \"minimal\" } } } }\n"
        "\n"
        "  [[hint.tunable]]\n"
        '  name = "thinking_level"\n'
        '  label = "Thinking"\n'
        '  path = "extra_body.google.thinking_config.thinking_level"\n'
        '  values = ["minimal", "high"]\n'
        '  default = "minimal"\n'
        '  help = "off/on"\n',
        encoding="utf-8",
    )
    loaded = hints_mod.load_model_hints(str(p))
    assert loaded["ok"], loaded.get("error")
    t = loaded["hints"][0]["tunables"]
    assert len(t) == 1
    assert t[0]["name"] == "thinking_level"
    assert t[0]["label"] == "Thinking"
    assert t[0]["path"] == "extra_body.google.thinking_config.thinking_level"
    assert t[0]["values"] == ["minimal", "high"]
    assert t[0]["default"] == "minimal"
    assert t[0]["help"] == "off/on"


def test_load_hints_bad_tunables(tmp_path):
    def load(body):
        p = tmp_path / "hints.toml"
        p.write_text("[[hint]]\n" "pattern = 'x'\n" "suggest = { a = true }\n\n"
                     + body, encoding="utf-8")
        return hints_mod.load_model_hints(str(p))

    good = ('  [[hint.tunable]]\n'
            '  name = "t"\n'
            '  path = "extra_body.a.b"\n'
            '  values = ["1", "2"]\n'
            '  default = "1"\n')
    assert load(good)["ok"]
    # default not among values
    assert not load(good.replace('default = "1"', 'default = "3"'))["ok"]
    # single value: not a control
    assert not load(good.replace('values = ["1", "2"]', 'values = ["1"]'))["ok"]
    # bad name (leading digit)
    assert not load(good.replace('name = "t"', 'name = "1t"'))["ok"]
    # bad path segment (space)
    assert not load(good.replace('"extra_body.a.b"', '"extra_body. a.b"'))["ok"]
    # path too deep
    assert not load(good.replace('"extra_body.a.b"',
                                 '"a.b.c.d.e.f.g"'))["ok"]
    # duplicate names
    assert not load(good + good)["ok"]
    # a bad tunable never hides the rest of a valid hint
    out = load(good.replace('name = "t"', 'name = ""'))
    assert not out["ok"]
    assert out["hints"] == []


def test_baked_gemma4_tunable():
    """The baked Gemma-4 hint declares its thinking_level tunable, and the
    suggested default sits at the tunable's path (so the select and the
    suggestion can never disagree)."""
    loaded = hints_mod.load_model_hints()
    assert loaded["ok"], loaded.get("error")
    h = hints_mod.match_hint("models/gemma-4-31b-it", loaded["hints"])
    assert h is not None
    tunables = {t["name"]: t for t in h["tunables"]}
    assert "thinking_level" in tunables
    t = tunables["thinking_level"]
    assert t["values"] == ["minimal", "high"]
    assert t["default"] == "minimal"
    assert _get_path(h["suggest"], t["path"]) == t["default"]