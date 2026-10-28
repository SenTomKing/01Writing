"""v5续写授权、快照、计量、清理与可重复并发故障测试。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Event
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from writing_server.generation import Generation, ModelFailure
from writing_server.jobs import process_task, dispatch, recover
from writing_server.tables import Attempt, Candidate, Knowledge, SourceIndex, Task, TaskEvent, now
from writing_server.maintenance import cleanup_indexes
from writing_assistant.models import RevisionCandidate
from test_fullstack import enqueue
from test_v4 import indexed
from writing_server.vector_store import persist_index


def missing(client, app, settings, doc):
    task=enqueue(client,doc)
    def fail(*args,**kwargs):raise ModelFailure("evidence_needed","需要补充范围")
    process_task(app.state.database,settings,task['id'],fail)
    return task


def follow(client,doc,parent,key='clarify-one',**extra):
    return client.post(f"/api/v1/tasks/{parent['id']}/clarify",headers={'Idempotency-Key':key},json={
        'clarification':'仅使用最终名单，按正式参加者计算。','base_version_id':doc['current_version_id'],**extra})


def test_clarification_fresh_snapshot_idempotent_and_old_task_immutable(client,app,settings,doc):
    task=missing(client,app,settings,doc)
    source=client.post('/api/v1/knowledge',json={'kind':'source','title':'最终名单','content':'正式参加者24人。'}).json()
    old=client.get('/api/v1/tasks/'+task['id']).json()
    response=follow(client,doc,task,source_ids=[source['id']])
    assert response.status_code==202,response.text
    child=response.json()
    assert child['previous_task_id']==task['id'] and child['clarification']['text'].startswith('仅使用')
    assert child['telemetry']['request_id']==response.headers['X-Request-ID']
    assert follow(client,doc,task,source_ids=[source['id']]).json()['id']==child['id']
    assert follow(client,doc,task,source_ids=[]).status_code==409
    client.patch('/api/v1/knowledge/'+source['id'],json={'kind':'source','title':'新名单','content':'未来更新为30人。'})
    with app.state.database.sessions() as session:
        assert session.get(Task,child['id']).context['sources'][0]['content']=='正式参加者24人。'
    captured=[]
    def generate(*args,**kwargs):
        captured.append(kwargs)
        return Generation(RevisionCandidate('正式参加者24人。','按最终名单修订'),None)
    assert process_task(app.state.database,settings,child['id'],generate)
    assert '仅使用最终名单' in captured[0]['instruction']
    assert '需要补充范围' in captured[0]['instruction']
    assert client.post('/api/v1/tasks/'+child['id']+'/accept').status_code==200
    assert client.get(f"/api/v1/documents/{doc['id']}/export?format=txt").text=='正式参加者24人。'
    assert client.get('/api/v1/tasks/'+task['id']).json()==old


def test_clarification_validation_ownership_and_stale_version(client,app,settings,doc):
    parent=missing(client,app,settings,doc)
    assert follow(client,doc,parent,clarification='  ').status_code==422
    assert follow(client,doc,parent,source_ids=['missing']).status_code==404
    assert client.post('/api/v1/tasks/'+parent['id']+'/retry',headers={'Idempotency-Key':'not-clarified'}).status_code==409
    with TestClient(app) as other:
        me=other.post('/api/v1/auth/register',json={'username':'other','password':'other-password-123'}).json()
        other.headers['X-CSRF-Token']=me['csrf_token']
        assert follow(other,doc,parent).status_code==404
        assert other.get('/api/v1/statistics').json()['tasks']==0
    client.post(f"/api/v1/documents/{doc['id']}/versions",json={'content':'新版','base_version_id':doc['current_version_id']})
    assert follow(client,doc,parent).status_code==409


def test_clarification_requires_terminal_information_gap_and_has_four_round_limit(client,app,settings,doc):
    task=enqueue(client,doc)
    assert follow(client,doc,task).status_code==409
    for i in range(4):
        def fail(*a,**k):raise ModelFailure('evidence_needed','仍缺资料')
        process_task(app.state.database,settings,task['id'],fail)
        result=follow(client,doc,task,key=f'round-{i}-key')
        assert result.status_code==202,result.text
        task=result.json()
    process_task(app.state.database,settings,task['id'],fail)
    assert follow(client,doc,task,key='fifth-round').status_code==409


def test_failed_followup_retry_preserves_selected_evidence_and_clarification(client,app,settings,doc):
    parent=missing(client,app,settings,doc)
    child=follow(client,doc,parent).json()
    def fail(*a,**k):raise ModelFailure('model_unavailable','不可用')
    process_task(app.state.database,settings,child['id'],fail)
    result=client.post('/api/v1/tasks/'+child['id']+'/retry',headers={'Idempotency-Key':'retry-child'})
    assert result.status_code==202,result.text
    assert result.json()['previous_task_id']==child['id']
    assert result.json()['clarification']==child['clarification']


def test_statistics_deduplicates_replayed_agent_calls_and_marks_unknown(client,app,doc):
    task=enqueue(client,doc)
    with app.state.database.sessions.begin() as session:
        row=session.get(Task,task['id']); row.status='failed';row.error_code='model_timeout';row.finished_at=now()
        calls=[{'budget_slot':0,'usage':{'total_tokens':100},'duration_ms':10}]
        for number in (1,2):
            session.add(Attempt(task_id=row.id,number=number,claim_token=f'claim-{number}',provider='deepseek',model='unit',
                prompt_version='unit',usage={'calls':deepcopy(calls),'reserved_calls':2},duration_ms=20,outcome='failed'))
        row.checkpoint={'calls':calls,'reserved_calls':2}
    result=client.get('/api/v1/tasks/'+task['id']).json()['telemetry']
    assert result['reported_tokens']==100 and result['returned_calls']==1
    assert result['unconfirmed_calls']==1 and result['retries']==1 and result['amount'] is None
    stats=client.get('/api/v1/statistics').json()
    assert stats['reported_tokens']==100 and stats['acceptance_rate'] is None
    assert client.get('/api/v1/statistics?days=91').status_code==422


def test_cleanup_protects_task_snapshot_and_current_source_only_deletes_orphan(client,app,settings,doc):
    task=enqueue(client,doc)
    with app.state.database.sessions.begin() as session:
        row=session.get(Task,task['id']); owner=row.user_id
        protected=persist_index(session,owner,indexed(settings,identity='removed-but-used'))
        orphan=persist_index(session,owner,indexed(settings,identity='removed-unused'))
        current=persist_index(session,owner,indexed(settings,identity='current'))
        recent=persist_index(session,owner,indexed(settings,identity='recent'))
        session.add(Knowledge(id='current',user_id=owner,kind='source',title='仍有资料',content='正文'))
        row.context={'sources':[{'index_id':protected}]}
        for index_id in (protected,orphan,current):session.get(SourceIndex,index_id).created_at=now()-timedelta(days=8)
    with app.state.database.sessions.begin() as session:
        preview=cleanup_indexes(session,owner)
        assert preview['eligible_ids']==[orphan] and preview['protected']==1 and preview['deleted']==0
    with app.state.database.sessions.begin() as session:
        assert cleanup_indexes(session,owner,apply=True)['deleted']==1
    with app.state.database.sessions() as session:
        assert set(session.scalars(select(SourceIndex.id)))=={protected,current,recent}


def test_broker_outage_events_are_bounded_and_recovery_visible(client,app,doc):
    task=enqueue(client,doc);db=app.state.database
    def fail(_):raise OSError('never log connection credentials')
    for _ in range(3):
        with db.sessions.begin() as session:session.get(Task,task['id']).dispatch_after=now()-timedelta(seconds=1)
        assert dispatch(db,fail)==0
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.kind=='dispatch_failed'))==1
    with db.sessions.begin() as session:session.get(Task,task['id']).dispatch_after=now()-timedelta(seconds=1)
    assert dispatch(db,lambda _:None)==1
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.kind=='dispatch_restored'))==1


@pytest.mark.parametrize('iteration',range(5))
def test_two_workers_only_one_generates_and_stale_result_is_fenced(client,app,settings,doc,iteration):
    task=enqueue(client,doc);entered=Event();release=Event();calls=[]
    def generate(*a,**k):
        calls.append(1);entered.set();assert release.wait(10)
        return Generation(RevisionCandidate('旧执行结果','应被丢弃'),None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(process_task,app.state.database,settings,task['id'],generate)
        try:
            assert entered.wait(10)
            assert not pool.submit(process_task,app.state.database,settings,task['id'],generate).result(10)
            with app.state.database.sessions.begin() as session:
                session.get(Task,task['id']).lease_until=now()-timedelta(seconds=1)
            assert recover(app.state.database,settings)==1
            assert process_task(app.state.database,settings,task['id'])
        finally:release.set()
        assert not first.result(10)
    result=client.get('/api/v1/tasks/'+task['id']).json()
    assert len(calls)==1 and result['status']=='review' and result['candidate']['text']!='旧执行结果'
    assert result['runs'][0]['outcome']=='discarded'
    with app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Candidate))==1


def test_actual_worker_process_termination_recovers_without_duplicate_candidate(client,app,settings,doc,tmp_path):
    import os, subprocess, sys, time
    from sqlalchemy.engine import make_url
    task=enqueue(client,doc);marker=tmp_path/'entered'
    child=tmp_path/'worker.py'
    child.write_text("""import os,time
from pathlib import Path
from writing_server.settings import Settings
from writing_server.database import Database
from writing_server.jobs import process_task
settings=Settings.load()
def blocked(*a,**k):
 Path(os.environ['FAULT_MARKER']).write_text('claimed')
 time.sleep(60)
process_task(Database(settings),settings,os.environ['FAULT_TASK'],blocked)
""",encoding='utf-8')
    env=os.environ.copy();url=make_url(settings.database_url) if isinstance(settings.database_url,str) else settings.database_url
    env.update(WRITING_DATABASE_URL=url.render_as_string(hide_password=False),FAULT_MARKER=str(marker),FAULT_TASK=task['id'],WRITING_PROVIDER='demo')
    process=subprocess.Popen([sys.executable,str(child)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if marker.exists():break
            if process.poll() is not None:pytest.fail('子进程未成功领取任务')
            time.sleep(.05)
        assert marker.exists()
    finally:
        process.terminate();process.wait(timeout=10)
    # 缩短等待，不改变恢复器的条件；真实执行进程已经终止。
    with app.state.database.sessions.begin() as session:
        row=session.get(Task,task['id']);assert row.status=='running'
        row.lease_until=now()-timedelta(seconds=1)
    assert recover(app.state.database,settings)==1
    assert process_task(app.state.database,settings,task['id'])
    result=client.get('/api/v1/tasks/'+task['id']).json()
    assert result['status']=='review' and result['runs'][0]['outcome']=='interrupted'
    assert len(result['runs'])==2


def test_real_celery_publish_to_unavailable_broker_preserves_task(client,app,doc):
    import socket
    from celery import Celery
    task=enqueue(client,doc)
    # 绑定但不监听的本机临时端口保证拒绝连接，不关闭正式Redis。
    with socket.socket() as blocked:
        blocked.bind(('127.0.0.1',0));port=blocked.getsockname()[1]
        queue=Celery('fault-test',broker=f'redis://127.0.0.1:{port}/0')
        queue.conf.update(broker_connection_timeout=.2,broker_transport_options={'socket_connect_timeout':.2,'socket_timeout':.2},task_publish_retry=False)
        try:
            assert dispatch(app.state.database,lambda value:queue.send_task('fault.never',args=[value],retry=False))==0
        finally:queue.close()
    result=client.get('/api/v1/tasks/'+task['id']).json()
    assert result['status']=='queued' and result['candidate'] is None


def test_actual_provider_timeout_is_bounded_and_records_failure(client,app,settings,doc,monkeypatch):
    from dataclasses import replace
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from writing_server.generation import generate
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):time.sleep(.4);self.close_connection=True
        def log_message(self,*a):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    configured=replace(settings,api_key='test-not-a-secret',base_url=f'http://127.0.0.1:{server.server_port}/v1',model_timeout=.1)
    # 本机故障端点直连，绝不发送实际密钥。
    monkeypatch.setenv('NO_PROXY','127.0.0.1,localhost')
    task=enqueue(client,doc)
    def generate_real(_,**kwargs):return generate(configured,**{**kwargs,'provider':'deepseek'})
    try:
        for _ in range(settings.max_attempts):process_task(app.state.database,settings,task['id'],generate_real)
    finally:server.shutdown();server.server_close();thread.join(timeout=2)
    result=client.get('/api/v1/tasks/'+task['id']).json()
    assert result['status']=='failed' and result['error_code']=='model_timeout'
    assert result['candidate'] is None and len(result['runs'])==3
    assert result['telemetry']['unconfirmed_calls']==3


def test_legacy_skipped_audit_is_excluded_from_statistics(client,app,doc):
    task=enqueue(client,doc)
    usage={"reserved_calls":2, "calls":[{"budget_slot":0,"usage":{"total_tokens":10}},
        {"budget_slot":1,"usage":None,"purpose":"evidence_audit"}],
        "evidence_audit":{"warning":"没有参考证据，未进行事实核验"}}
    with app.state.database.sessions.begin() as session:
        row=session.get(Task,task['id']);row.checkpoint=deepcopy(usage)
        session.add(Attempt(task_id=row.id,number=1,claim_token='legacy',provider='deepseek',model='unit',
            prompt_version='unit',usage=usage,duration_ms=20,outcome='succeeded'))
    result=client.get('/api/v1/tasks/'+task['id']).json()['telemetry']
    assert result['returned_calls']==result['reserved_calls']==1
    assert result['unconfirmed_calls']==result['unmetered_calls']==0
    assert result['reported_tokens']==10
