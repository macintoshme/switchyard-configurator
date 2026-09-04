import pytest
from switchyard_config.models import ConfigState, Provider, Route
from switchyard_config.routes import (
    find_route_cycles,
    generate_toml,
    parse_routes_text,
)

def test_generate_toml_basic():
    prov = Provider(name='openai', display_name='OpenAI', endpoint='http://example', api_key_env='', api_key='', max_retries=2)
    prov.selected_models = ['model1']
    route = Route(name='r1', id='model1', type='passthrough', target='model1')
    state = ConfigState(providers=[prov], routes=[route])
    toml_str = generate_toml(state)
    assert '[llm_clients.openai]' in toml_str
    assert '[routes.r1]' in toml_str
    assert 'model1' in toml_str

def test_find_route_cycles_detects_cycle():
    r1 = Route(name='r1', id='A', type='passthrough', target='B')
    r2 = Route(name='r2', id='B', type='passthrough', target='A')
    state = ConfigState(providers=[], routes=[r1, r2])
    cycles = find_route_cycles(state)
    assert any(set(c) == {'A', 'B'} for c in cycles)

def test_parse_routes_text_roundtrip():
    prov = Provider(name='openai', display_name='OpenAI', endpoint='http://example', api_key_env='OPENAI_TOKEN', api_key='', max_retries=2)
    prov.selected_models = ['model1']
    route = Route(name='r1', id='model1', type='passthrough', target='model1')
    state = ConfigState(providers=[prov], routes=[route])
    toml_str = generate_toml(state)
    routes, providers, extras, err = parse_routes_text(toml_str)
    assert err is None
    assert [r.id for r in routes] == ['model1']
    assert [p.name for p in providers] == ['openai']
    assert providers[0].api_key_env == 'OPENAI_TOKEN'
    assert extras == {}

def test_parse_routes_text_invalid_toml():
    routes, providers, extras, err = parse_routes_text('this is [ not valid toml')
    assert err is not None
    assert routes == []
    assert providers == []
    assert extras == {}

def test_parse_routes_text_empty():
    routes, providers, extras, err = parse_routes_text('')
    assert err is None
    assert routes == []
    assert providers == []
    assert extras == {}

def test_status_reports_deployment_platform(monkeypatch):
    from configurator import state as state_mod

    monkeypatch.setattr(state_mod, "is_switchyard_running", lambda: False)
    monkeypatch.delenv("DEPLOYMENT_PLATFORM", raising=False)
    m = state_mod.ConfigManager()
    assert m.status()["platform"] == ""

    monkeypatch.setenv("DEPLOYMENT_PLATFORM", "kubernetes")
    assert m.status()["platform"] == "kubernetes"
