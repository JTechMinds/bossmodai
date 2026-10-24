"""ChatGPT sign-in, managed connection, credential isolation and Responses adapter."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.chatgpt import API_BASE, ChatGPTError
from core.chatgpt import auth, provider, store
from core.extensions.registry import set_enabled


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    db.close_connection()
    path = Path(os.environ['BOSSMOD_DB_PATH'])
    for suffix in ('', '-wal', '-shm'):
        Path(str(path) + suffix).unlink(missing_ok=True)
    db.init_db()
    config.reload()
    monkeypatch.setattr(store, 'extension_data_dir', lambda _: tmp_path / 'private')
    set_enabled('chatgpt', True)
    yield
    auth.cancel_login()
    db.close_connection()


def account(**updates):
    return {'client_id': 'oaiapp_test', 'subject': 'subject', 'email': 'test@example.com',
            'access_token': 'secret-access', 'refresh_token': 'secret-refresh', 'id_token': 'secret-id',
            'scopes': [auth.PLAN_SCOPE], 'expires_at': time.time() + 3600,
            'models': [{'slug': 'test-model', 'display_name': 'Test'}], **updates}


def saved_account(**updates):
    with store.locked_store() as data:
        data['accounts']['oaiapp_test'] = account(**updates)
        data['active'] = 'oaiapp_test'
        auth._ensure_connection(data, 'test-model')
        store.save_store(data)
        return data['connection_id']


def attempt(**updates):
    return {'state': 'state', 'nonce': 'nonce', 'verifier': 'verifier', 'expires_at': time.time() + 60,
            'redirect_uri': 'http://127.0.0.1:12345/auth/callback', 'client_id': None, 'subject': None, **updates}


def mock_login(monkeypatch):
    monkeypatch.setattr(auth, '_token_request', lambda fields: {
        'access_token': 'secret-access', 'refresh_token': 'secret-refresh', 'id_token': 'secret-id',
        'token_type': 'Bearer', 'scope': auth.PLAN_SCOPE, 'expires_in': 3600})
    monkeypatch.setattr(auth, '_validate_identity', lambda *args: {'sub': 'subject', 'email': 'test@example.com'})
    monkeypatch.setattr(auth, 'model_catalog', lambda token: [{'slug': 'test-model', 'display_name': 'Test'}])
    monkeypatch.setattr(provider, 'probe', lambda *args: None)


def callback_query(**updates):
    return {'state': ['state'], 'code': ['code'], 'client_id': ['oaiapp_test'], **updates}


def test_host_id_is_stable_and_credentials_are_encrypted():
    saved_account()
    with store.locked_store() as data:
        host_id = data['host_id']
    with store.locked_store() as data:
        assert data['host_id'] == host_id
    file = store._root() / 'credentials.enc'
    assert 'secret-access' not in file.read_text()
    assert 'secret-refresh' not in file.read_text()
    assert file.stat().st_mode & 0o777 == 0o600
    assert 'secret' not in json.dumps(auth.status())


def test_callback_creates_connection_and_reauth_preserves_id(monkeypatch):
    mock_login(monkeypatch)
    auth.accept_callback(callback_query(), attempt())
    first = db.list_connections()
    assert len(first) == 1
    assert first[0].name == 'Chat GPT' and first[0].api_base_url == API_BASE
    assert first[0].api_key is None
    auth.accept_callback(callback_query(), attempt(client_id='oaiapp_test', subject='subject'))
    assert [conn.id for conn in db.list_connections()] == [first[0].id]


@pytest.mark.parametrize('query, pending', [
    (callback_query(state=['wrong']), attempt()),
    (callback_query(), attempt(expires_at=0)),
    (callback_query(error=['access_denied']), attempt()),
    (callback_query(client_id=[]), attempt()),
    (callback_query(client_id=['dynamic_agent_client']), attempt()),
    (callback_query(), attempt(client_id='oaiapp_other')),
    (callback_query(code=['']), attempt()),
])
def test_invalid_callback_never_exchanges_code(monkeypatch, query, pending):
    calls = []
    monkeypatch.setattr(auth, '_token_request', lambda fields: calls.append(fields))
    with pytest.raises(ChatGPTError):
        auth.accept_callback(query, pending)
    assert not calls and not db.list_connections()


def test_declined_plan_permission_does_not_replace_active_account(monkeypatch):
    saved_account()
    mock_login(monkeypatch)
    monkeypatch.setattr(auth, '_token_request', lambda _: {'access_token': 'new', 'id_token': 'id', 'scope': 'openid'})
    with pytest.raises(ChatGPTError, match='Allow BossMod'):
        auth.accept_callback(callback_query(), attempt())
    assert auth.access_token() == 'secret-access'


def test_failed_inference_probe_creates_no_connection(monkeypatch):
    mock_login(monkeypatch)
    def fail(*args):
        raise ChatGPTError('Usage unavailable')
    monkeypatch.setattr(provider, 'probe', fail)
    with pytest.raises(ChatGPTError, match='Usage unavailable'):
        auth.accept_callback(callback_query(), attempt())
    assert not db.list_connections() and not auth.status()['connected']


def test_returning_identity_must_match(monkeypatch):
    mock_login(monkeypatch)
    with pytest.raises(ChatGPTError, match='selected ChatGPT account'):
        auth.accept_callback(callback_query(), attempt(client_id='oaiapp_test', subject='other'))
    assert not db.list_connections()


def test_id_token_signature_audience_expiration_and_nonce(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(jwt, 'PyJWKClient', lambda *a, **k: NS(get_signing_key_from_jwt=lambda t: NS(key=private.public_key())))
    claims = {'iss': auth.ISSUER, 'aud': 'oaiapp_test', 'sub': 'subject', 'exp': time.time() + 60, 'nonce': 'nonce'}
    def encoded(**updates):
        return jwt.encode({**claims, **updates}, private, algorithm='RS256')
    assert auth._validate_identity(encoded(), 'oaiapp_test', 'nonce')['sub'] == 'subject'
    for token in (encoded(aud='wrong'), encoded(exp=1), encoded(nonce='wrong'), encoded(iss='wrong')):
        with pytest.raises(ChatGPTError):
            auth._validate_identity(token, 'oaiapp_test', 'nonce')
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ChatGPTError):
        auth._validate_identity(jwt.encode(claims, other, algorithm='RS256'), 'oaiapp_test', 'nonce')


def test_refresh_rotates_and_is_not_repeated(monkeypatch):
    saved_account(expires_at=0)
    calls = []
    def tokens(fields):
        calls.append(fields)
        return {'access_token': 'new-access', 'refresh_token': 'new-refresh', 'token_type': 'Bearer', 'expires_in': 3600}
    monkeypatch.setattr(auth, '_token_request', tokens)
    assert auth.access_token() == auth.access_token() == 'new-access'
    assert len(calls) == 1 and calls[0]['client_id'] == 'oaiapp_test'
    with store.locked_store() as data:
        assert data['accounts']['oaiapp_test']['refresh_token'] == 'new-refresh'


def test_disabling_blocks_calls_and_signout_retains_connection(monkeypatch):
    cid = saved_account()
    set_enabled('chatgpt', False)
    with pytest.raises(ChatGPTError, match='Enable'):
        auth.access_token()
    class Offline:
        def __enter__(self):
            raise httpx.ConnectError('offline')
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: Offline())
    assert auth.sign_out()['revoked'] is False
    assert db.get_connection_by_id(cid) is not None
    assert not auth.status()['connected']
    with store.locked_store() as data:
        assert data['accounts']['oaiapp_test']['subject'] == 'subject'
        assert 'refresh_token' not in data['accounts']['oaiapp_test']


def test_loopback_pkce_state_single_use_and_port_cleanup(monkeypatch):
    mock_login(monkeypatch)
    urls = []
    monkeypatch.setattr(auth.webbrowser, 'open', lambda url: urls.append(url) or True)
    assert auth.begin_login()['pending']
    params = parse_qs(urlsplit(urls[0]).query)
    assert params['client_id'] == ['dynamic_agent_client']
    assert params['code_challenge_method'] == ['S256']
    assert params['agent_name_hint'] == ['BossMod AI']
    redirect = params['redirect_uri'][0]
    assert redirect.startswith('http://127.0.0.1:') and redirect.endswith('/auth/callback')
    with httpx.Client(trust_env=False) as client:
        assert client.get(redirect + '?state=wrong&code=bad').status_code == 400
        assert auth.status()['pending']
        response = client.get(redirect + '?' + urlencode({'state': params['state'][0], 'code': 'code', 'client_id': 'oaiapp_test'}))
        assert response.status_code == 200 and 'Connected' in response.text
    assert not auth.status()['pending'] and auth.status()['connected']


def test_api_protects_managed_connection_and_hides_tokens(monkeypatch):
    monkeypatch.setattr(provider, "probe", lambda *args: None)
    cid = saved_account()
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    with TestClient(app) as client:
        assert client.get('/api/extensions/chatgpt/account').status_code == 401
        client.headers[LOCAL_API_TOKEN_HEADER] = db.ensure_local_api_token()
        for path in ('/api/extensions/chatgpt/account', '/api/connections'):
            result = client.get(path)
            assert result.status_code == 200
            assert 'secret-access' not in result.text and 'secret-refresh' not in result.text
        conn = client.get('/api/connections').json()[0]
        assert conn['managed_by'] == 'chatgpt' and not conn['has_api_key']
        assert client.patch('/api/connections/' + cid, json={'api_base_url': 'https://evil.test'}).status_code == 409
        assert client.post('/api/connections/' + cid + '/duplicate').status_code == 409
        assert client.put('/api/extensions/chatgpt/model', json={'model': 'not-offered'}).status_code == 409
        assert client.put('/api/extensions/chatgpt/model', json={'model': 'test-model'}).status_code == 200
        extension = next(item for item in client.get('/api/extensions').json() if item['id'] == 'chatgpt')
        assert extension['account_connection'] and extension['enabled']


def test_history_translation_preserves_personality_and_images():
    translated = provider.response_input([
        {'role': 'system', 'content': 'You are the researcher.'},
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Look'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,a'}}]},
        {'role': 'assistant', 'content': 'Result'},
    ])
    assert translated[0]['role'] == 'developer'
    assert translated[0]['content'] == 'You are the researcher.'
    assert translated[1]['content'][1]['type'] == 'input_image'
    assert translated[2]['content'] == 'Result'


class FakeStream:
    def __init__(self, events):
        self.events = iter(events)
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    def __aiter__(self):
        return self
    async def __anext__(self):
        try:
            return next(self.events)
        except StopIteration:
            raise StopAsyncIteration


def mock_responses(monkeypatch, events):
    requests = []
    class Client:
        def __init__(self, **kwargs):
            self.responses = self
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def create(self, **kwargs):
            requests.append(kwargs)
            return FakeStream(events)
    monkeypatch.setattr(provider, 'AsyncOpenAI', Client)
    return requests


@pytest.mark.asyncio
async def test_runtime_uses_responses_and_requires_completed_event(monkeypatch):
    saved_account()
    final = NS(output=[NS(type='message', content=[NS(type='output_text', text='{"action":"done"}')])],
               model='test-model', usage=NS(input_tokens=10, output_tokens=4, total_tokens=14))
    requests = mock_responses(monkeypatch, [NS(type='response.completed', response=final)])
    from core.llm.client import completion
    result = await completion('test-model', [{'role': 'system', 'content': 'Personality'}],
                              api_base=API_BASE, temperature=0.5, max_tokens=100)
    assert result.content == '{"action":"done"}' and result.total_tokens == 14
    assert requests[0]['store'] is False and requests[0]['stream'] is True
    assert 'temperature' not in requests[0] and 'max_output_tokens' not in requests[0]
    assert requests[0]['input'][0]['role'] == 'developer'


@pytest.mark.asyncio
@pytest.mark.parametrize('events, expected', [
    ([NS(type='response.output_text.delta', delta='partial')], 'interrupted'),
    ([NS(type='response.failed', response=NS(error=NS(code='subscription_sharing_usage_limit_exceeded')))], 'usage limit'),
    ([NS(type='response.incomplete', response=NS(error=None))], 'couldn’t complete'),
])
async def test_partial_and_failed_streams_never_become_agent_replies(monkeypatch, events, expected):
    saved_account()
    mock_responses(monkeypatch, events)
    from core.llm.client import LLMError
    with pytest.raises(LLMError, match=expected):
        await provider.completion('test-model', [{'role': 'user', 'content': 'Work'}])


def test_chatgpt_also_powers_system_ai(monkeypatch):
    saved_account()
    calls = []
    def complete(token, model, messages, **kwargs):
        calls.append((token, model, messages, kwargs))
        return '{"agent_id":"chosen"}'
    monkeypatch.setattr(provider, 'complete_sync', complete)
    from core.llm.system_completion import complete_text
    assert complete_text([{'role': 'system', 'content': 'Route this message'}]) == '{"agent_id":"chosen"}'
    assert calls[0][0] == 'secret-access' and calls[0][1] == 'test-model'


@pytest.mark.asyncio
async def test_stalled_stream_times_out_and_releases_budget(monkeypatch):
    saved_account()
    mock_responses(monkeypatch, [])
    async def stall(self):
        await asyncio.sleep(1)
    monkeypatch.setattr(FakeStream, '__anext__', stall)
    db.set_setting('llm_stall_timeout_seconds', '0.01', 'llm')
    config.reload()
    from core.llm.client import LLMTimeoutError
    with pytest.raises(LLMTimeoutError):
        await provider.completion('test-model', [{'role': 'user', 'content': 'Work'}])


def test_refresh_is_serialized_between_threads(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    saved_account(expires_at=0)
    calls = []
    def refresh(fields):
        calls.append(fields['refresh_token'])
        time.sleep(0.02)
        return {'access_token': 'rotated', 'refresh_token': 'rotated-refresh', 'token_type': 'Bearer', 'expires_in': 3600}
    monkeypatch.setattr(auth, '_token_request', refresh)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: auth.access_token(), range(4)))
    assert results == ['rotated'] * 4 and calls == ['secret-refresh']


def test_cancelled_login_cannot_create_connection(monkeypatch):
    monkeypatch.setattr(auth.webbrowser, 'open', lambda _: True)
    auth.begin_login()
    auth.cancel_login()
    assert not auth.status()['pending'] and not db.list_connections()


def test_token_exchange_uses_public_client_pkce_and_resource(monkeypatch):
    captured = []
    def respond(request):
        captured.append(request)
        return httpx.Response(200, json={'access_token': 'token', 'token_type': 'Bearer'})
    real_client = httpx.Client
    monkeypatch.setattr(auth.httpx, 'Client', lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    result = auth._token_request({'grant_type': 'authorization_code', 'client_id': 'oaiapp_test',
        'code': 'code', 'code_verifier': 'verifier', 'redirect_uri': 'http://127.0.0.1:12345/auth/callback'})
    assert result['access_token'] == 'token'
    request = captured[0]
    assert str(request.url) == auth.TOKEN_URL
    form = parse_qs(request.content.decode())
    assert form['resource'] == [auth.RESOURCE] and form['code_verifier'] == ['verifier']
    assert 'client_secret' not in form and form['client_id'] == ['oaiapp_test']


@pytest.mark.asyncio
async def test_real_sdk_stream_transport(monkeypatch):
    from openai import AsyncOpenAI
    saved_account()
    captured = []
    completed = {'id': 'resp_test', 'object': 'response', 'created_at': 1, 'status': 'completed',
        'model': 'test-model', 'output': [{'id': 'msg_test', 'type': 'message', 'role': 'assistant',
            'status': 'completed', 'content': [{'type': 'output_text', 'text': 'Done', 'annotations': []}]}],
        'usage': {'input_tokens': 2, 'output_tokens': 1, 'total_tokens': 3}}
    def respond(request):
        captured.append(request)
        payload = 'data: ' + json.dumps({'type': 'response.completed', 'response': completed, 'sequence_number': 1}) + '\n\ndata: [DONE]\n\n'
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=payload)
    def client(**kwargs):
        return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(provider, 'AsyncOpenAI', client)
    result = await provider.completion('test-model', [{'role': 'system', 'content': 'Researcher'},
        {'role': 'assistant', 'content': 'Previous answer'}, {'role': 'user', 'content': 'Continue'}])
    assert result.content == 'Done' and result.total_tokens == 3
    request = captured[0]
    assert str(request.url) == 'https://api.openai.com/v1/responses'
    assert request.headers['authorization'] == 'Bearer secret-access'
    body = json.loads(request.content)
    assert body['input'][0] == {'role': 'developer', 'content': 'Researcher'}
    assert body['input'][1] == {'role': 'assistant', 'content': 'Previous answer'}
    assert body['store'] is False and body['stream'] is True
