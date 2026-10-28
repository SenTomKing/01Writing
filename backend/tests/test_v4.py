"""真实数据库查询、不可变快照与证据不足停止的回归。模型调用使用可控替身。"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest
from sqlalchemy import select, func

from writing_server import agent, vector_store
from writing_server.evidence_context import assemble, retrieve_evidence
from writing_server.retrieval import embedding_id
from writing_server.settings import Settings
from writing_server.tables import Knowledge, SourceIndex, SourceChunk, Task, User
from writing_server.generation import ModelFailure


def configured(settings):
    return replace(settings, embedding_key='test', embedding_url='https://embedding.invalid/v1', embedding_model='unit')


def indexed(settings, content='公开研究有12名参与者。', identity='source-one'):
    return {'id':identity,'title':'研究记录','content':content,'updated_at':'2026-10-28T00:00:00Z',
        'chunk_index':{'version':2,'hash':hashlib.sha256(content.encode()).hexdigest(),
            'embedding_id':embedding_id(settings),'dimension':3,
            'chunks':[{'offset':0,'text':content,'page':1,'vector':[1.,0.,0.]}]}}


def task_with_index(client,app,settings,doc):
    settings=configured(settings)
    source=indexed(settings)
    response=client.post('/api/v1/knowledge',json={'kind':'source','title':source['title'],'content':source['content']})
    assert response.status_code==201
    source_id=response.json()['id']
    with app.state.database.sessions.begin() as session:
        session.get(Knowledge,source_id).chunk_index=source['chunk_index']
    response=client.post(f"/api/v1/documents/{doc['id']}/tasks",headers={'Idempotency-Key':'vectors-v4'},json={
        'instruction':'根据资料改正参与人数','base_version_id':doc['current_version_id'],
        'strategy':'agent','source_ids':[source_id]})
    assert response.status_code==202,response.text
    task_id=response.json()['id']
    with app.state.database.sessions() as session:
        task=session.get(Task,task_id)
        sources=deepcopy(task.context['sources']);owner=task.user_id
    return settings,task_id,owner,sources


def test_pgvector_exact_and_immutable_task(client,app,settings,doc,monkeypatch):
    settings,task_id,owner,sources=task_with_index(client,app,settings,doc)
    assert 'vector' not in sources[0]['chunk_index']['chunks'][0]
    monkeypatch.setattr(vector_store,'embed',lambda *args:[[1.,0.,0.]])
    rank=vector_store.task_ranker(app.state.database,settings,task_id,owner)
    scores,mode,_=rank(sources,'受试者数量')
    assert mode=='pgvector_exact' and scores=={'S1:0':1.0}
    assert client.patch('/api/v1/knowledge/'+sources[0]['id'],json={
        'kind':'source','title':'已修订记录','content':'新的内容不覆盖历史'}).status_code==200
    assert client.delete('/api/v1/knowledge/'+sources[0]['id']).status_code==200
    assert rank(sources,'数量')[0]==scores
    with app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(SourceIndex))==1
        assert session.scalar(select(SourceChunk)).embedding==[1.,0.,0.]


def test_pgvector_authorization_rejects_foreign_and_unselected_index(client,app,settings,doc,monkeypatch):
    settings,task_id,owner,sources=task_with_index(client,app,settings,doc)
    with app.state.database.sessions.begin() as session:
        other=User(username='vector-other',password_hash='not-a-login');session.add(other);session.flush()
        other_id=other.id
        foreign=indexed(settings,'另一个账户的保密资料。','foreign')
        foreign['index_id']=vector_store.persist_index(session,other.id,foreign)
        unselected=indexed(settings,'本人未选择的另一份资料。','not-selected')
        unselected['index_id']=vector_store.persist_index(session,owner,unselected)
    calls=[]
    monkeypatch.setattr(vector_store,'embed',lambda *args:calls.append(1) or [[1.,0.,0.]])
    rank=vector_store.task_ranker(app.state.database,settings,task_id,owner)
    assert rank([foreign,unselected],'资料')[0]=={} and calls==[]
    assert rank([*sources,foreign,unselected],'资料')[0]=={'S1:0':1.0}
    with pytest.raises(ValueError,match='授权'):
        vector_store.task_ranker(app.state.database,settings,task_id,other_id)(sources,'资料')


def test_pgvector_model_and_content_mismatch_no_charge(client,app,settings,doc,monkeypatch):
    settings,task_id,owner,sources=task_with_index(client,app,settings,doc)
    def no_call(*args):pytest.fail('不兼容索引不能触发收费')
    monkeypatch.setattr(vector_store,'embed',no_call)
    rank=vector_store.task_ranker(app.state.database,replace(settings,embedding_model='different'),task_id,owner)
    assert rank(sources,'查询')[1]=='keyword'
    altered=deepcopy(sources);altered[0]['content']='伪造内容'
    assert vector_store.task_ranker(app.state.database,settings,task_id,owner)(altered,'查询')[0]=={}


def test_index_dedup_rebuild_and_rollback(client,app,settings):
    settings=configured(settings)
    with app.state.database.sessions.begin() as session:
        owner=session.scalar(select(User.id));source=indexed(settings)
        first=vector_store.persist_index(session,owner,source)
        assert vector_store.persist_index(session,owner,source)==first
    changed=deepcopy(source);changed['title']='新标题'
    with app.state.database.sessions.begin() as session:
        second=vector_store.persist_index(session,owner,changed)
        assert second!=first
    invalid=deepcopy(source);invalid['title']='失败重建';invalid['chunk_index']['chunks'][0]['vector']=[float('nan')]
    with pytest.raises(ValueError):
        with app.state.database.sessions.begin() as session:vector_store.persist_index(session,owner,invalid)
    with app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(SourceIndex))==2
        assert session.get(SourceChunk,(first,0)).content==source['content']


def test_context_windows_keep_offsets_page_and_budget():
    text='无关背景。'*90+'项目在秋季招募了12名参与者。仅统计正式入组人员。'+'其他材料。'*90
    piece={'ref':'S1:200','offset':200,'text':text,'title':'材料','page':2}
    chosen=assemble([piece,piece], '招募参与者',budget=500)
    assert any('12名参与者' in p['text'] for p in chosen)
    assert sum(len(p['text']) for p in chosen)<=500
    assert len({p['ref'] for p in chosen})==len(chosen)
    for p in chosen:
        assert p['text']==text[p['offset']-200:p['offset']-200+len(p['text'])]
        assert p['page']==2
    assert assemble([piece],'查询',budget=1)==[]


def test_context_explicit_degrade_and_zero_matches():
    sources=[{'title':'资料','content':'实验人数为12人。','updated_at':'today'}]
    def broken(*args):raise RuntimeError('db unavailable')
    output=retrieve_evidence(sources,'实验人数',ranker=broken)
    assert output['mode']=='keyword' and '降级' in output['note']
    assert output['chunks'][0]['source_version']=='today'
    assert output['evidence_status']=='unassessed'
    assert retrieve_evidence(sources,'xyzxyz')['chunks']==[]


def assessment(status='sufficient',quote='公开研究有12名参与者。'):
    return {'status':status,'requirements':[{'requirement':'参与人数','evidence':[{'ref':'S1:0','quote':quote}]}],
        'missing':[],'explanation':'数量有原文支持'}


@pytest.mark.parametrize('kind',['forged','no_evidence','missing','huge_gap'])
def test_assessment_rejects_invalid_claims(kind):
    value=assessment();state={'seen':{'S1:0':{'text':'公开研究有12名参与者。'}}}
    if kind=='forged':value['requirements'][0]['evidence'][0]['quote']='不存在的另一个数字'
    elif kind=='no_evidence':value['requirements'][0]['evidence']=[]
    elif kind=='missing':value['missing']=['人数缺失']
    else:value['missing']=['x'*301]
    with pytest.raises(ValueError):agent.assess(agent.EvidenceAssessment.model_validate(value),state)


def scripted(items):
    def completion(messages,tools):
        name,args=items.pop(0)
        return {'usage':{'total_tokens':20},'message':{'role':'assistant','tool_calls':[
            {'id':'call','type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}]}}
    return completion


def test_submit_requires_assessment_and_read_invalidates_it():
    source=indexed(configured(Settings(database_url='unused',migration_url=None)))
    submission={'text':'公开研究有12名参与者。[S1:0]','reason':'依据资料','citations':[{'ref':'S1:0','quote':source['content']}]}
    items=[('read_source',{'ref':'S1:0'}),('submit_revision',submission),
        ('assess_evidence',assessment()),('read_source',{'ref':'S1:0'}),('submit_revision',submission),
        ('request_clarification',{'question':'请补充确认'})]
    with pytest.raises(ModelFailure) as info:
        agent.run_agent(Settings(database_url='unused',migration_url=None),source='12人',instruction='核实人数',
            feedback=[],provider='demo',model='demo',context={'sources':[source]},completion=scripted(items))
    assert info.value.code=='evidence_needed'
    assert [t['outcome'] for t in info.value.usage['trace'] if t['tool']=='submit_revision']==['未通过','未通过']


def test_missing_evidence_stops_without_candidate():
    value=assessment('missing');value['requirements'][0]['evidence']=[];value['missing']=['长期随访结果']
    items=[('assess_evidence',value),('request_clarification',{'question':'请提供长期随访记录'})]
    with pytest.raises(ModelFailure) as info:
        agent.run_agent(Settings(database_url='unused',migration_url=None),source='长期疗效好',instruction='按资料核实',
            feedback=[],provider='demo',model='demo',context={'sources':[]},completion=scripted(items))
    assert info.value.code=='evidence_needed'
    assert info.value.usage['evidence_assessment']['status']=='missing'


def test_conflict_assessment_pauses_immediately():
    source=indexed(configured(Settings(database_url='unused',migration_url=None)))
    value=assessment('conflict');value['explanation']='两份同日记录分别写12人与18人，无法自行裁决。'
    items=[('read_source',{'ref':'S1:0'}),('assess_evidence',value)]
    with pytest.raises(ModelFailure) as info:
        agent.run_agent(Settings(database_url='unused',migration_url=None),source='人数待核对',instruction='比较资料',
            feedback=[],provider='demo',model='demo',context={'sources':[source]},completion=scripted(items))
    assert info.value.code=='evidence_needed'
    assert '12人与18人' in str(info.value)
    assert info.value.usage['reserved_calls']==2
    assert info.value.usage['evidence_assessment']['status']=='conflict'
