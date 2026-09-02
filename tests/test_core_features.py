import pytest
from switchyard_config.models import ConfigState, Provider, Route
from switchyard_config.routes import generate_toml, find_route_cycles

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
