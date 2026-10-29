"""个人凭证完整链路；真实PostgreSQL、假供应商，不消费任何真实API额度。"""
import base64
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from writing_server.app import create_app
from writing_server import credentials, research
from writing_server.generation import Generation, ModelFailure
from writing_server.jobs import process_task
from writing_server.tables import Task, UserCredential, ResearchJob
from writing_assistant.models import RevisionCandidate

KEYS = {'deepseek_key': 'sk-personal-deepseek-secret', 'embedding_key': 'sk-personal-silicon-secret'}
PATH = '/api/v1/account/model-settings'


@pytest.fixture
def personal(settings):
    settings = replace(settings, provider='deepseek', api_key='sk-owner-do-not-use', search_key='owner-search',
        embedding_key='owner-embedding', base_url='https://wrong.example',
        credential_master_key=Fernet.generate_key().decode())
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post('/api/v1/auth/register', json={'username': 'personal', 'password': 'test-personal-password'})
        assert response.status_code == 201
        user = response.json()
        client.headers['X-CSRF-Token'] = user['csrf_token']
        yield app, client, user, settings


def document(client):
    return client.post('/api/v1/documents', json={'title': '个人文档', 'content': '实验有12份数据。'}).json()


def submit(client, doc, strategy='workflow'):
    response = client.post(f"/api/v1/documents/{doc['id']}/tasks", headers={'Idempotency-Key': str(uuid4())},
        json={'instruction': '保持数字', 'base_version_id': doc['current_version_id'], 'strategy': strategy})
    assert response.status_code == 202, response.text
    return response.json()


def test_no_keys_blocks_paid_work_but_documents_remain_available(personal):
    app, client, user, settings = personal
    doc = document(client)
    result = client.post(f"/api/v1/documents/{doc['id']}/tasks", headers={'Idempotency-Key':'no-key-test'},
        json={'instruction':'润色', 'base_version_id':doc['current_version_id']})
    assert result.status_code == 409
    assert client.get(PATH).json()['configured'] is False
    assert client.get('/api/v1/meta').json()['web_search_available'] is False
    assert client.post('/api/v1/research/imports', json={'kind':'file','title':'资料','filename':'a.txt',
        'content_base64':base64.b64encode(b'text').decode()}).status_code == 409
    with app.state.database.sessions() as session:
        assert not session.scalars(select(Task)).all()
        assert not session.scalars(select(ResearchJob)).all()
    assert client.get('/api/v1/documents').json()['total'] == 1


def test_credentials_encrypted_masked_and_bound_to_owner(personal):
    app, client, user, settings = personal
    response = client.post(PATH, json=KEYS)
    assert response.status_code == 200, response.text
    assert response.json()['configured']
    assert not any(key in response.text+client.get(PATH).text for key in KEYS.values())
    with app.state.database.sessions() as session:
        row = session.get(UserCredential,user['id'])
        assert not any(key in row.ciphertext for key in KEYS.values())
        personal_settings = credentials.resolve(session,settings,user['id'])
        assert personal_settings.api_key == KEYS['deepseek_key']
        assert personal_settings.embedding_key == KEYS['embedding_key']
        assert personal_settings.search_key == '' and personal_settings.effective_search_provider == 'deepseek'
        assert personal_settings.base_url == 'https://api.deepseek.com'
        assert not any(key in repr(personal_settings) for key in KEYS.values())
    assert client.get('/api/v1/meta').json()['semantic_search_available']
    with TestClient(app) as other:
        me = other.post('/api/v1/auth/register',json={'username':'other','password':'test-other-password'}).json()
        other.headers['X-CSRF-Token']=me['csrf_token']
        assert not other.get(PATH).json()['configured']
        assert not other.get('/api/v1/meta').json()['semantic_search_available']
        with app.state.database.sessions.begin() as session:
            source = session.get(UserCredential,user['id'])
            session.add(UserCredential(user_id=me['id'], revision=source.revision, ciphertext=source.ciphertext))
        assert not other.get(PATH).json()['configured']
    assert client.get(PATH).json()['configured']


def test_validation_csrf_and_unauthenticated_never_echo_secrets(personal):
    app, client, user, settings = personal
    assert client.post(PATH,json=KEYS,headers={'X-CSRF-Token':'wrong'}).status_code == 403
    assert client.post(PATH,json=KEYS,headers={'Origin':'https://attacker.example'}).status_code == 403
    for invalid in [{**KEYS,'extra':KEYS['deepseek_key']},{**KEYS,'embedding_key':None}, {**KEYS,'embedding_key':'wrong\nsecret'}, {'deepseek_key':KEYS['deepseek_key']}]:
        response = client.post(PATH,json=invalid)
        assert response.status_code == 422
        assert not any(key in response.text for key in KEYS.values())
        assert 'wrong\nsecret' not in response.text
    with TestClient(app) as stranger:
        assert stranger.get(PATH).status_code == 401
        assert stranger.post(PATH,json=KEYS).status_code == 401
    with app.state.database.sessions() as session:
        assert session.get(UserCredential,user['id']) is None


@pytest.mark.parametrize('action',['replace','delete','wrong_master','legacy'])
def test_queued_jobs_fail_closed_if_credentials_changed(personal,action):
    app, client, user, settings = personal
    assert client.post(PATH,json=KEYS).status_code == 200
    task = submit(client,document(client))
    if action == 'replace':
        assert client.post(PATH,json={**KEYS,'deepseek_key':'sk-new-personal-secret'}).status_code == 200
    elif action == 'delete':
        assert client.delete(PATH).status_code == 200
    elif action == 'wrong_master':
        settings = replace(settings,credential_master_key=Fernet.generate_key().decode())
    else:
        with app.state.database.sessions.begin() as session:
            row=session.get(Task,task['id']);row.context={k:v for k,v in row.context.items() if k!='credential_revision'}
    called=[]
    process_task(app.state.database, settings, task['id'], generator=lambda *a,**kw:called.append(True))
    assert called == []
    result = client.get('/api/v1/tasks/'+task['id']).json()
    assert result['status']=='failed' and result['error_code'].startswith('credential')
    assert not any(key in str(result) for key in KEYS.values())


@pytest.mark.parametrize('strategy',['direct','workflow','agent'])
def test_each_generation_strategy_receives_account_keys(personal,strategy):
    app, client, user, settings = personal
    client.post(PATH,json=KEYS)
    task = submit(client,document(client),strategy)
    with app.state.database.sessions() as session:
        assert not any(key in str(session.get(Task,task['id']).context) for key in KEYS.values())
    captured=[]
    def generator(effective, **kwargs):
        captured.append((effective.api_key,effective.embedding_key,effective.search_key))
        return Generation(RevisionCandidate('实验有12份数据。','保持原意'),None)
    assert process_task(app.state.database, settings, task['id'], generator=generator)
    assert captured==[(KEYS['deepseek_key'],KEYS['embedding_key'],'')]
    assert client.get('/api/v1/tasks/'+task['id']).json()['status']=='review'


def test_import_worker_uses_owner_keys_and_changed_keys_stop_it(personal,monkeypatch):
    app, client, user, settings = personal
    client.post(PATH,json=KEYS)
    def enqueue():
        result=client.post('/api/v1/research/imports',json={'kind':'file','title':'资料','filename':'a.txt',
            'content_base64':base64.b64encode('资料有12份数据'.encode()).decode()})
        assert result.status_code==202,result.text
        return result.json()['id']
    captured=[]
    original=research.build_index
    def index(effective,content,pages):
        captured.append(effective.embedding_key)
        return original(replace(effective,embedding_key=''),content,pages)
    monkeypatch.setattr(research,'build_index',index)
    first=enqueue();assert research.process(app.state.database,settings,first)
    assert captured==[KEYS['embedding_key']]
    second=enqueue();client.delete(PATH)
    assert not research.process(app.state.database,settings,second)
    assert captured==[KEYS['embedding_key']]


def test_connection_test_safe_and_uses_two_private_services(personal,monkeypatch):
    app, client, user, settings = personal
    client.post(PATH,json=KEYS)
    seen=[]
    class Remote:
        def __init__(self,**kwargs):seen.append(kwargs);self.models=self;self.embeddings=self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def list(self):return []
        def create(self,**kwargs):return SimpleNamespace(data=[SimpleNamespace(embedding=[1,2])])
    monkeypatch.setattr('openai.OpenAI',Remote)
    result=client.post(PATH+'/test')
    assert result.status_code==200 and result.text.count('连接成功')==2
    assert [s['api_key'] for s in seen]==list(KEYS.values())
    assert not any(key in result.text for key in KEYS.values())


def test_invalid_master_and_ciphertext_never_fall_back(personal):
    app, client, user, settings = personal
    client.post(PATH,json=KEYS)
    with app.state.database.sessions.begin() as session:
        row=session.get(UserCredential,user['id']);row.ciphertext='corrupted'
    with app.state.database.sessions() as session:
        with pytest.raises(ModelFailure,match='无法读取'):
            credentials.resolve(session,settings,user['id'])
        with pytest.raises(ModelFailure,match='存储未配置'):
            credentials.cipher(replace(settings,credential_master_key=''))


def test_demo_clears_real_services(settings):
    result=credentials.resolve(None,replace(settings,provider='demo',api_key='owner',search_key='owner',embedding_key='owner'),'any')
    assert not result.api_key and not result.embedding_key and not result.search_available
