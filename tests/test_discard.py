"""Discarding unsaved changes (configurator/state.py discard_draft)."""

import pytest

from switchyard_config import files
from configurator import state as state_mod


@pytest.fixture
def manager(monkeypatch, tmp_path):
    monkeypatch.setattr(state_mod, "is_switchyard_running", lambda: False)
    for name in ("ROUTES_TOML", "ROUTES_DRAFT", "ENV_DRAFT", "PROVIDER_META_FILE",
                 "PROVIDER_META_DRAFT"):
        monkeypatch.setattr(state_mod.C, name, tmp_path / name)
    # switchyard_config.files imports these names into its own namespace.
    for name in ("ROUTES_DRAFT", "ENV_DRAFT", "PROVIDER_META_DRAFT"):
        monkeypatch.setattr(files, name, tmp_path / name)
    m = state_mod.ConfigManager()
    m.load_errors = []
    return m


def _provider_payload(name="vllm"):
    return {"name": name, "display_name": name, "endpoint": "http://example.invalid/v1"}


def test_discard_clears_drafts_and_reloads_saved_config(manager):
    assert manager.has_unsaved_changes() is False

    r = manager.upsert_provider(-1, _provider_payload())
    assert r["ok"], r.get("error")
    assert manager.has_unsaved_changes() is True
    assert manager.state.providers and manager.state.providers[0].name == "vllm"

    res = manager.discard_draft()
    assert res["ok"] is True
    assert res["had_changes"] is True
    # Draft files are gone and the state is back to the saved (empty) config.
    assert manager.has_unsaved_changes() is False
    assert manager.state.providers == []
    assert manager.snapshot()["providers"] == []
    assert "unsaved" not in res["message"]


def test_discard_without_changes_is_a_noop(manager):
    before = manager.snapshot()
    res = manager.discard_draft()
    assert res["ok"] is True
    assert res["had_changes"] is False
    assert manager.snapshot() == before