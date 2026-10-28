"""供应商请求协议、切分引用位置与检索降级的回归检查。"""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from writing_server import retrieval, web_search
from writing_server.knowledge import chunks
from writing_server.settings import Settings


def settings(**kwargs):
    return Settings(database_url='unused', migration_url=None, **kwargs)


def test_search_provider_selection_and_disabled():
    base = settings(provider='deepseek', api_key='fake-deepseek')
    assert base.effective_search_provider == 'deepseek' and base.search_available
    assert replace(base, search_provider='none').search_available is False
    assert replace(base, provider='demo').search_available is False
    assert replace(base, base_url='https://another-provider.example').search_available is False
    assert replace(base, search_key='fake-tavily').effective_search_provider == 'tavily'
    assert replace(base, search_provider='tavily').search_available is False
    assert settings(search_provider='deepseek').search_available is False


@pytest.mark.parametrize('provider', ['deepseek', 'tavily'])
def test_search_request_and_result_normalization(monkeypatch, provider):
    captured = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, count):
            assert count == 250_001
            return json.dumps({'results': [
                {'url': 'http://127.0.0.1/private'},
                {'url': 'https://example.com/a', 'title': '标题', 'snippet': 'DeepSeek摘要', 'content': 'Tavily摘要'},
                {'url': 'https://example.com/a', 'title': '重复'},
                None]}).encode()
    class Opener:
        def open(self, request, timeout):
            captured.append(request)
            assert timeout == 12
            return Response()
    monkeypatch.setattr(web_search, 'build_opener', lambda handler: Opener())
    result = web_search.search_web(settings(search_provider=provider, api_key='ds', search_key='tv'), ' Python ')
    assert len(result) == 1 and result[0]['url'] == 'https://example.com/a'
    request = captured[0]
    assert request.get_header('Authorization') == ('Bearer ds' if provider == 'deepseek' else 'Bearer tv')
    body = json.loads(request.data)
    assert body['query'] == 'Python'
    if provider == 'deepseek':
        assert request.full_url == 'https://api.deepseek.com/v1/tools/search'
        assert body == {'query': 'Python', 'count': 5, 'need_content': False}
        assert result[0]['snippet'] == 'DeepSeek摘要'
    else:
        assert request.full_url == 'https://api.tavily.com/search'
        assert body['max_results'] == 5 and not body['include_raw_content']
        assert result[0]['snippet'] == 'Tavily摘要'


@pytest.mark.parametrize('query', ['', '  ', '字'*129, None])
def test_invalid_search_query_never_calls_vendor(monkeypatch, query):
    monkeypatch.setattr(web_search, 'build_opener', lambda *args: pytest.fail('不能联网'))
    with pytest.raises(ValueError, match='128'):
        web_search.search_web(settings(search_provider='deepseek', api_key='fake'), query)


def test_search_hides_vendor_error_and_rejects_redirect(monkeypatch):
    def fail(*args): raise RuntimeError('credential and upstream response must not escape')
    monkeypatch.setattr(web_search, 'build_opener', fail)
    with pytest.raises(ValueError) as error:
        web_search.search_web(settings(search_key='fake'), 'Python')
    assert 'credential' not in str(error.value) and error.value.__suppress_context__
    assert web_search.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example') is None


def test_overlap_preserves_offsets_coverage_and_pdf_page_boundaries():
    content = '甲'*700 + '。' + '乙'*1200 + '\n' + '丙'*1200
    pages = [{'page': 1, 'offset': 0}, {'page': 2, 'offset': 1902}]
    pieces = retrieval.split_content(content, pages, overlap=120)
    coverage = set()
    for i, piece in enumerate(pieces):
        start, text = piece['offset'], piece['text']
        assert text == content[start:start+len(text)] and 0 < len(text) <= 900
        assert not start < 1902 < start+len(text)
        assert piece['page'] == (1 if start < 1902 else 2)
        coverage.update(range(start, start+len(text)))
        if i:
            previous = pieces[i-1]
            assert 0 <= previous['offset']+len(previous['text'])-start <= 120
    assert coverage == set(range(len(content)))
    assert pieces[0]['text'].endswith('。')


def test_invalid_chunk_configuration_and_empty_input():
    for kwargs in [{'max_chars': 10}, {'overlap': -1}, {'overlap': 500}]:
        with pytest.raises(ValueError): retrieval.split_content('测试', **kwargs)
    assert retrieval.split_content('') == []
    assert retrieval.embed(settings(), []) == []


def test_stale_index_is_not_used_for_text_or_vector(monkeypatch):
    config = settings(embedding_key='fake', embedding_url='https://example.com/v1', embedding_model='model')
    monkeypatch.setattr(retrieval, 'embed', lambda *a: [[1., 0.]])
    index = retrieval.build_index(config, '旧内容')
    source = {'content': '新内容', 'title': '资料', 'chunk_index': index}
    monkeypatch.setattr(retrieval, 'embed', lambda *a: pytest.fail('过期索引不能查询向量'))
    assert list(chunks([source]))[0]['text'] == '新内容'
    assert retrieval.retrieve([source], '新内容', config)['mode'] == 'keyword'


def test_changed_embedding_model_and_dimensions_do_not_fake_hybrid(monkeypatch):
    config = settings(embedding_key='fake', embedding_url='https://example.com/v1', embedding_model='model')
    monkeypatch.setattr(retrieval, 'embed', lambda *a: [[1., 0.]])
    source = {'content': '资料依据', 'title': '标题', 'chunk_index': retrieval.build_index(config, '资料依据')}
    assert retrieval.retrieve([source], '依据', replace(config, embedding_model='new-model'))['mode'] == 'keyword'
    monkeypatch.setattr(retrieval, 'embed', lambda *a: [[1., 0., 0.]])
    result = retrieval.retrieve([source], '依据', config)
    assert result['mode'] == 'keyword' and '降级' in result['note']


def test_embedding_float_request_and_response_validation(monkeypatch):
    import openai
    config = settings(embedding_key='fake', embedding_url='https://example.com/v1', embedding_model='model')
    rows = [SimpleNamespace(index=1, embedding=[0., 1.]), SimpleNamespace(index=0, embedding=[1., 0.])]
    class Client:
        def __init__(self, **kwargs): self.embeddings = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs):
            assert kwargs['encoding_format'] == 'float'
            assert kwargs['input'] == ['文本一', '文本二']
            return SimpleNamespace(data=rows)
    monkeypatch.setattr(openai, 'OpenAI', Client)
    assert retrieval.embed(config, ['文本一', '文本二']) == [[1., 0.], [0., 1.]]
    rows[0].index = 0
    with pytest.raises(ValueError, match='索引'): retrieval.embed(config, ['文本一', '文本二'])
    rows[0].index = 1
    rows[0].embedding = [float('nan'), 1.]
    with pytest.raises(ValueError, match='数值'): retrieval.embed(config, ['文本一', '文本二'])


def test_batch_dimension_change_cannot_save_partial_index(monkeypatch):
    config = settings(embedding_key='fake', embedding_url='https://example.com/v1', embedding_model='model')
    calls = []
    def embed(config, texts):
        calls.append(len(texts))
        return [[1., 0.] if len(calls) == 1 else [1., 0., 0.] for _ in texts]
    monkeypatch.setattr(retrieval, 'embed', embed)
    with pytest.raises(ValueError, match='不同维度'): retrieval.build_index(config, '长文'*20000)
    assert len(calls) == 2


def test_deepseek_search_meta_and_task_authorization(app, client, settings, doc):
    # 现有 API 设置仍是无外网的测试环境；用户未授权时不会开放联网工具。
    assert client.get('/api/v1/meta').json()['web_search_available'] is False
    response = client.post(f"/api/v1/documents/{doc['id']}/tasks", headers={'Idempotency-Key': 'search-disabled'},
        json={'strategy': 'agent', 'instruction': '搜索资料', 'base_version_id': doc['current_version_id'], 'allow_web': True})
    assert response.status_code == 422
