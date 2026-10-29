"""单机运维核对与备份恢复演练；不打印凭证，不修改正式数据库内容。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
DOCKER=shutil.which('docker') or (r'E:\Docker\Desktop\resources\bin\docker.exe' if os.name=='nt' else 'docker')
COMPOSE=[DOCKER,'compose','--env-file','.env.deploy']
def run(args,**kwargs):
    result=subprocess.run(args,cwd=ROOT,timeout=120,capture_output=True,**kwargs)
    if result.returncode:raise RuntimeError('运维命令失败，已停止；请在本机检查服务状态（此处不输出可能含凭证的原始错误）。')
    return result.stdout
def sql(query,database='writing'):
    return run(COMPOSE+['exec','-T','postgres','psql','-U','postgres','-d',database,'-v','ON_ERROR_STOP=1','-Atc',query]).decode().strip()
def free_memory():
    if os.name=='nt':
        output=run(['powershell','-NoProfile','-Command','(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory'])
        return int(output.strip())/1024**2
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):return int(line.split()[1])/1024**2
    return None
def preflight():
    free=free_memory()
    if free is not None and free<4:raise RuntimeError('可用内存不足4GB，停止构建或升级')
    run(COMPOSE+['config','--quiet'])
    active=int(sql("select (select count(*) from wa_tasks where status in ('queued','running'))+(select count(*) from wa_research_jobs where status in ('queued','running'))"))
    return {'free_memory_gb':round(free,2) if free is not None else None,'active_jobs':active,
        'schema':sql('select version_num from alembic_version'),
        'postgres':sql('show server_version'),'pgvector':sql("select extversion from pg_extension where extname='vector'"),
        'collation':sql("select coalesce(datcollversion,'')||'/'||coalesce(pg_database_collation_actual_version(oid),'') from pg_database where datname=current_database()")}
def fingerprints(database):
    tables=sql("select tablename from pg_tables where schemaname='public' and (tablename like 'wa_%' or tablename='alembic_version') order by tablename",database).splitlines()
    result={}
    for table in tables:
        if not re.fullmatch('[a-z0-9_]+',table):raise ValueError('非法表名')
        value=sql(f"select count(*)||':'||md5(coalesce(string_agg(md5(row_to_json(t)::text),'' order by md5(row_to_json(t)::text)),'')) from public.{table} t",database)
        result[table]=value
    return result
def restore_drill():
    state=preflight()
    if state['active_jobs']:raise RuntimeError('仍有任务执行，先等待任务完成')
    # 操作者应先停止API/worker/dispatcher，避免备份后又有新写入。
    running=run(COMPOSE+['ps','--services','--status','running']).decode().splitlines()
    if any(s in running for s in ('api','worker','dispatcher')):
        raise RuntimeError('请先停止API、worker、dispatcher，再做一致性恢复演练')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    target='writing_restore_'+uuid4().hex
    assert re.fullmatch('writing_restore_[0-9a-f]{32}',target)
    folder=ROOT/'private/backups';folder.mkdir(parents=True,exist_ok=True)
    backup=folder/f'before-upgrade-{stamp}.dump'
    if backup.exists():raise RuntimeError('备份已存在，拒绝覆盖')
    before=fingerprints('writing');start=time.monotonic()
    payload=run(COMPOSE+['exec','-T','postgres','pg_dump','-U','postgres','-d','writing','-Fc','--no-owner','--no-privileges'])
    if len(payload)<1000:raise RuntimeError('备份异常小，停止')
    backup.write_bytes(payload);dump_seconds=time.monotonic()-start
    created=False
    try:
        sql(f'CREATE DATABASE {target} TEMPLATE template0');created=True;start=time.monotonic()
        run(COMPOSE+['exec','-T','postgres','pg_restore','-U','postgres','-d',target,'--no-owner','--no-acl','--exit-on-error','--single-transaction'],input=payload)
        restore_seconds=time.monotonic()-start
        after=fingerprints(target)
        if before!=after:raise RuntimeError('恢复后的表内容校验不一致；备份已保留')
        if before!=fingerprints('writing'):raise RuntimeError('演练期间源数据发生变化，请重新验收')
        report={'created_at':datetime.now(timezone.utc).isoformat(),'backup_file':backup.name,'backup_bytes':len(payload),
            'sha256':hashlib.sha256(payload).hexdigest(),'tables':len(before),'rows':sum(int(v.split(':')[0]) for v in before.values()),
            'all_table_hashes_match':True,'dump_seconds':round(dump_seconds,3),'restore_seconds':round(restore_seconds,3),
            'database':state,'scope':'同一数据库实例中新建隔离数据库恢复；不等于异机灾备或权限配置恢复',
            'notes':'备份排除角色所有权和ACL；角色由deploy/init-db.sh重建，另执行check-db.sql验证。'}
        (folder/f'restore-report-{stamp}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return report
    finally:
        if created:
            # 只删除本函数刚创建且未承载应用连接的唯一演练库；不使用FORCE。
            sql(f'DROP DATABASE {target}')
def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['preflight','restore-drill']);args=parser.parse_args()
    print(json.dumps(preflight() if args.action=='preflight' else restore_drill(),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
