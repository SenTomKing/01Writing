"""冻结的小型最终验收。默认离线；--live显式付费，断点文件避免无意重复。"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from writing_server.generation import generate, ModelFailure
from writing_server.settings import Settings

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();folder=Path(__file__).parent
    data_path=folder/'dataset.json';protocol=json.loads((folder/'protocol.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(data_path.read_bytes()).hexdigest()==protocol['dataset_sha256'],'冻结数据发生变化'
    if args.output.exists():raise SystemExit('结果已存在，禁止覆盖或自动重复计费')
    data=json.loads(data_path.read_text(encoding='utf-8'));settings=replace(Settings.load(),embedding_key='',embedding_url='',embedding_model='',search_key='',search_provider='none')
    if args.live and not settings.api_key:raise SystemExit('未配置真实模型')
    rows=[];calls=0;tokens=0
    def budget():
        nonlocal calls
        if calls>=protocol['max_requests'] or tokens>=protocol['max_total_reported_tokens']:raise RuntimeError('预算上限')
        calls+=int(args.live)
    def save():
        report={'dataset_sha256':protocol['dataset_sha256'],'created_at':datetime.now(timezone.utc).isoformat(),
            'mode':'live' if args.live else 'demo','model':settings.model,'calls':calls,'reported_tokens':tokens,
            'planned':36,'completed':len(rows),'amount':None,'human_acceptance_rate':None,'results':rows}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        tmp=args.output.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');tmp.replace(args.output)
    for task in data['tasks']:
        for strategy in protocol['strategies']:
            if calls>=protocol['max_requests'] or tokens>=protocol['max_total_reported_tokens']:break
            context={'sources':[{'id':key,'title':key,'content':data['sources'][key]} for key in task['sources']],'memories':[],'allow_web':False}
            started=time.monotonic();row={'id':task['id'],'group':task['group'],'strategy':strategy,'semantic_review':'pending'}
            try:
                result=generate(settings,source=task['source'],instruction=task['instruction'],feedback=[],provider='deepseek' if args.live else 'demo',
                    model=settings.model,strategy=strategy,context=context,on_call=budget)
                text=result.candidate.text
                checks={**{f'包含:{term}':term in text for term in task['required']},**{f'不包含:{term}':term not in text for term in task['forbidden']}}
                row.update(text=text,reason=result.candidate.reason,usage=result.usage,checks=checks,
                    program_passed=all(checks.values()) if not task['expect_pause'] else None)
            except Exception as exc:
                row.update(error=getattr(exc,'code','evaluation_failed'),error_message=str(exc) if isinstance(exc,ModelFailure) else '评测运行失败或预算耗尽',
                    usage=getattr(exc,'usage',None),program_passed=bool(task['expect_pause'] and getattr(exc,'code',None)=='evidence_needed'))
            row['duration_ms']=int((time.monotonic()-started)*1000)
            tokens+=sum((c.get('usage') or {}).get('total_tokens',0) for c in (row.get('usage') or {}).get('calls',[]))
            rows.append(row);save();print(f"{len(rows)}/36 {task['id']} {strategy} {row.get('error','完成')} calls={calls} tokens={tokens}",flush=True)
    save()
if __name__=='__main__':main()
