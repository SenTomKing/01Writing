import base64
from copy import deepcopy
from datetime import timedelta
import json

import pytest
from fastapi.testclient import TestClient

from writing_server import research, retrieval, network, evidence, agent
from writing_server.extraction import extract
from writing_server.tables import ResearchJob, now
from writing_server.settings import Settings


@pytest.mark.parametrize('url',['http://127.0.0.1','https://[::1]','http://169.254.169.254/latest','http://10.0.0.1',
    'file:///etc/passwd','https://user:pass@example.com','https://example.com:444','https://localhost','https://a.local','https://example.com\\@127.0.0.1'])
def test_reject_nonpublic_urls(url):
    with pytest.raises(ValueError):network.public_url(url)


def test_dns_rebinding_mixed_addresses_rejected(monkeypatch):
    monkeypatch.setattr(network.socket,'getaddrinfo',lambda *a,**k:[(0,0,0,'',('93.184.216.34',443)),(0,0,0,'',('127.0.0.1',443))])
    with pytest.raises(ValueError):network.resolve_public('public.example',443)


def test_redirect_to_private_never_connects():
    calls=[]
    class Connection:
        def __init__(self,host,ip,*args):calls.append(ip)
        def request(self,*args,**kwargs):pass
        def getresponse(self):return self
        status=302
        def getheader(self,key,*args):return 'http://127.0.0.1/private' if key=='Location' else None
        def close(self):pass
    with pytest.raises(ValueError):network.fetch_page('https://public.example',resolver=lambda *a:'93.184.216.34',connection=Connection)
    assert calls==['93.184.216.34']


def test_html_removes_scripts_and_full_split_keeps_all_content():
    result=extract('<h1>标题</h1><script>秘密指令</script><p>真实正文</p>'.encode(),'web','text/html')
    assert '秘密' not in result['content'] and '真实正文' in result['content']
    content=('短段落\n\n'*9000)[:100000]
    pieces=retrieval.split_content(content)
    assert ''.join(p['text'] for p in pieces)==content
    assert all(content[p['offset']:p['offset']+len(p['text'])]==p['text'] for p in pieces)


def test_hybrid_semantics_and_fallback_are_explicit(monkeypatch):
    settings=Settings(database_url='unused',migration_url=None,embedding_key='test',embedding_model='test',embedding_url='https://embedding.example/v1')
    monkeypatch.setattr(retrieval,'embed',lambda settings,texts:[[1.0,0.0] for _ in texts])
    index=retrieval.build_index(settings,'服务器延迟过高')
    sources=[{'title':'性能','content':'服务器延迟过高','chunk_index':index}]
    result=retrieval.retrieve(sources,'响应很慢',settings)
    assert result['mode']=='hybrid' and result['chunks'][0]['text']=='服务器延迟过高'
    monkeypatch.setattr(retrieval,'embed',lambda *a:(_ for _ in ()).throw(RuntimeError()))
    result=retrieval.retrieve(sources,'延迟',settings)
    assert result['mode']=='keyword' and '降级' in result['note'] and result['chunks']


def test_evidence_cannot_invent_claim_or_quote():
    source=[{'ref':'S1:0','text':'调查对象为本校学生'}]
    finding={'claim':'所有学生喜欢产品','verdict':'insufficient','ref':'S1:0','quote':'调查对象为本校学生','explanation':'范围不能证明喜好'}
    result=evidence.check_review({'findings':[finding]},'所有学生喜欢产品',source)
    assert result['findings'][0]['verdict']=='insufficient'
    for key,value in [('claim','原文不存在'),('quote','95%的学生喜欢'),('ref','S9:0')]:
        bad={**finding,key:value}
        with pytest.raises(ValueError):evidence.check_review({'findings':[bad]},'所有学生喜欢产品',source)


def upload(client,text='调查对象为本校学生。'):
    response=client.post('/api/v1/research/imports',json={'kind':'file','title':'测试资料','filename':'资料.md','content_base64':base64.b64encode(text.encode()).decode()})
    assert response.status_code==202,response.text
    return response.json()['id']


def test_import_dedup_and_task_snapshot(client,app,settings,doc):
    first=upload(client)
    assert research.process(app.state.database,settings,first)
    assert not research.process(app.state.database,settings,first)
    second=upload(client)
    assert research.process(app.state.database,settings,second)
    items=client.get('/api/v1/knowledge').json()
    assert len(items)==1 and items[0]['provenance']['filename']=='资料.md'
    task=client.post(f"/api/v1/documents/{doc['id']}/tasks",headers={'Idempotency-Key':'snapshot-research'},json={
        'instruction':'按资料改写','base_version_id':doc['current_version_id'],'strategy':'agent','source_ids':[items[0]['id']]}).json()
    assert task['context_items'][0]['title']=='测试资料'
    assert client.delete('/api/v1/knowledge/'+items[0]['id']).status_code==200
    assert client.get('/api/v1/tasks/'+task['id']).json()['context_items']
    with app.state.database.sessions() as session:
        assert session.get(ResearchJob,first).payload=={}


def test_import_account_scope_and_cancel_in_flight(client,app,settings,monkeypatch):
    job=upload(client)
    with TestClient(app) as other:
        registered=other.post('/api/v1/auth/register',json={'username':'other_research','password':'test-password-12345'}).json()
        other.headers['X-CSRF-Token']=registered['csrf_token']
        assert other.get('/api/v1/research/imports').json()==[]
        assert other.post('/api/v1/research/imports/'+job+'/cancel').status_code==404
    def slow(*args):
        assert client.post('/api/v1/research/imports/'+job+'/cancel').status_code==200
        return {'version':1,'chunks':[]}
    monkeypatch.setattr(research,'build_index',slow)
    assert not research.process(app.state.database,settings,job)
    assert client.get('/api/v1/knowledge').json()==[]
    assert client.get('/api/v1/research/imports').json()[0]['status']=='cancelled'


def test_reindex_does_not_overwrite_concurrent_edit(client,app,settings,monkeypatch):
    job=upload(client);assert research.process(app.state.database,settings,job)
    entry=client.get('/api/v1/knowledge').json()[0]
    job=client.post('/api/v1/research/imports',json={'kind':'reindex','title':'重建','knowledge_id':entry['id']}).json()['id']
    original=research.build_index
    def edit_while_indexing(*args):
        assert client.patch('/api/v1/knowledge/'+entry['id'],json={'kind':'source','title':'新内容','content':'后来的新内容'}).status_code==200
        return original(*args)
    monkeypatch.setattr(research,'build_index',edit_while_indexing)
    assert not research.process(app.state.database,settings,job)
    entry=client.get('/api/v1/knowledge').json()[0]
    assert entry['content']=='后来的新内容' and entry['index_mode']=='keyword'


def test_import_lease_recovery_is_bounded(client,app):
    job=upload(client)
    with app.state.database.sessions.begin() as session:
        row=session.get(ResearchJob,job);row.status='running';row.attempts=2;row.lease_until=now()-timedelta(seconds=1)
    research.dispatch(app.state.database,lambda job:None)
    with app.state.database.sessions() as session:
        row=session.get(ResearchJob,job)
        assert row.status=='failed' and row.payload=={}


def test_web_authorization_required_and_import_validation(client,doc):
    response=client.post(f"/api/v1/documents/{doc['id']}/tasks",headers={'Idempotency-Key':'web-unauthorized'},json={
        'instruction':'搜索','strategy':'agent','base_version_id':doc['current_version_id'],'allow_web':True})
    assert response.status_code==422
    assert client.post('/api/v1/research/imports',json={'kind':'url','title':'私网','url':'http://127.0.0.1'}).status_code==422
    assert client.post('/api/v1/research/imports',json={'kind':'file','title':'损坏','filename':'a.txt','content_base64':'###'}).status_code==422


def test_web_tools_snapshot_and_partial_recovery(monkeypatch):
    settings=Settings(database_url='unused',migration_url=None,search_key='fake')
    counts={'search':0,'fetch':0}
    def search(*args):counts['search']+=1;return [{'url':'https://example.com/report','title':'报告','snippet':'摘要'}]
    def fetch(*args):counts['fetch']+=1;return '样本数为12。'.encode(),'text/plain','https://example.com/report'
    monkeypatch.setattr(agent,'search_web',search);monkeypatch.setattr(agent,'fetch_page',fetch)
    responses=[('search_web',{'query':'样本'}),('read_web',{'url':'https://example.com/report'}),
        ('assess_evidence',{'status':'sufficient','requirements':[{'requirement':'样本数','evidence':[{'ref':'S1:0','quote':'样本数为12。'}]}],'missing':[],'explanation':'样本数量有逐字依据'}),
        ('submit_revision',{'text':'样本数为12。[S1:0]','reason':'按来源纠错','citations':[{'ref':'S1:0','quote':'样本数为12。'}]})]
    def completion(messages,tools):
        name,args=responses.pop(0)
        return {'message':{'role':'assistant','tool_calls':[{'id':'reused-id','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]},'usage':{'total_tokens':10}}
    saved=[]
    def persist(state,message):
        if message=='已保存工具结果' and state.get('web_sources'):
            saved.append(deepcopy(state));raise InterruptedError()
    kwargs=dict(source='样本数为10。',instruction='按资料纠错',feedback=[],provider='demo',model='test',context={'sources':[],'memories':[],'allow_web':True},completion=completion)
    with pytest.raises(InterruptedError):agent.run_agent(settings,**kwargs,save=persist)
    result=agent.run_agent(settings,**kwargs,checkpoint=saved[-1])
    assert counts=={'search':1,'fetch':1}
    assert result.usage['web_sources'][0]['provenance']['url']=='https://example.com/report'
    assert result.candidate.text=='样本数为12。[S1:0]'


def test_pdf_extract_page_and_scanned_rejection():
    import sys
    import io
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=300,height=300)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 30 250 Td (Research sample 12 students.) Tj ET')
    page[NameObject('/Contents')]=stream
    buffer=io.BytesIO();writer.write(buffer)
    if sys.platform=='win32':
        with pytest.raises(ValueError):extract(buffer.getvalue(),'test.pdf')
    else:
        parsed=extract(buffer.getvalue(),'test.pdf')
        assert '12 students' in parsed['content'] and parsed['pages']==[{'page':1,'offset':0}]
        blank=PdfWriter();blank.add_blank_page(width=100,height=100);buffer=io.BytesIO();blank.write(buffer)
        with pytest.raises(ValueError):extract(buffer.getvalue(),'scanned.pdf')


def test_audit_failed_validation_keeps_paid_usage():
    def completion(messages):
        assert '不意味着资料陈述一律不可信' in messages[0]['content']
        return {'findings':[{'claim':'不存在','verdict':'supported','ref':'S1:0','quote':'依据','explanation':'错误'}]}, {'total_tokens':123}
    report,usage=evidence.audit(Settings(database_url='unused',migration_url=None),'候选',[{'ref':'S1:0','text':'依据'}],provider='demo',model='test',completion=completion)
    assert report['status']=='unavailable' and usage['total_tokens']==123


def test_gzip_webpage_and_decompression_bomb_are_bounded():
    import gzip
    class Connection:
        status=200
        def __init__(self,*args):self.sent=False
        def request(self,*args,**kwargs):pass
        def getresponse(self):return self
        def getheader(self,key,default=None):return {'Content-Type':'text/html','Content-Encoding':'gzip'}.get(key,default)
        def read1(self,size):
            if self.sent:return b''
            self.sent=True;return self.payload
        def close(self):pass
    Connection.payload=gzip.compress(b'<p>public evidence</p>')
    raw,_,_=network.fetch_page('https://public.example',resolver=lambda *a:'93.184.216.34',connection=Connection)
    assert raw==b'<p>public evidence</p>'
    Connection.payload=gzip.compress(b'x'*(network.MAX_BYTES+1))
    with pytest.raises(ValueError,match='2MB'):
        network.fetch_page('https://public.example',resolver=lambda *a:'93.184.216.34',connection=Connection)
