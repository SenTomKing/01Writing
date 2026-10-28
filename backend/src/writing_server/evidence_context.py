"""预算内证据装配：有界窗口、原文位置、去重及显式来源信息。"""
import re
from .knowledge import terms, chunks
from .retrieval import retrieve

VERSION='evidence-context-v4.0'


def windows(piece, query, max_chars=420):
    text=piece['text']
    if len(text)<=max_chars:
        return [piece]
    # 以句段为候选窗口，保留相邻句；在纯词法局部排序失败时保留片段开头。
    spans=[];start=0
    for match in re.finditer(r'[。！？!?；;\n]',text):
        if match.end()>start:
            spans.append((start,match.end()));start=match.end()
    if start<len(text):spans.append((start,len(text)))
    q=terms(query)
    ranked=sorted(enumerate(spans),key=lambda pair:(-len(q & terms(text[pair[1][0]:pair[1][1]])),pair[0]))
    result=[]
    for i,(a,b) in ranked[:2]:
        if b-a>max_chars:b=a+max_chars
        # 相邻限定语能放下才补全，不把大段前置背景硬塞进窗口。
        if i+1<len(spans) and spans[i+1][1]-a<=max_chars:b=spans[i+1][1]
        if i and b-spans[i-1][0]<=max_chars:a=spans[i-1][0]
        offset=piece['offset']+a
        result.append({**piece,'offset':offset,'ref':piece['ref'].split(':')[0]+':'+str(offset),'text':text[a:b]})
    return result


def assemble(ranked,query,budget=1800,max_pieces=6):
    selected=[];used=0
    for piece in ranked:
        for window in windows(piece,query):
            prefix=window['ref'].split(':')[0]
            a=window['offset'];b=a+len(window['text'])
            overlap=any(p['ref'].split(':')[0]==prefix and max(a,p['offset'])<min(b,p['offset']+len(p['text'])) for p in selected)
            if overlap or used+len(window['text'])>budget:
                continue
            selected.append(window);used+=len(window['text'])
            if len(selected)>=max_pieces:return selected
    return selected


def retrieve_evidence(sources,query,settings=None,ranker=None,budget=1800):
    pieces=list(chunks(sources));q=terms(query)
    lexical=sorted(pieces,key=lambda p:(-len(q & terms(p['title']+' '+p['text'])),p['ref']))
    lexical=[p for p in lexical if q & terms(p['title']+' '+p['text'])]
    scores={p['ref']:1/(60+i) for i,p in enumerate(lexical,1)}
    mode,note='keyword','关键词检索'
    if ranker:
        try:
            semantic,mode,note=ranker(sources,query)
            ordered=sorted(semantic,key=lambda ref:(-semantic[ref],ref))[:10]
            for i,ref in enumerate(ordered,1):scores[ref]=scores.get(ref,0)+1/(60+i)
        except Exception:
            mode,note='keyword','数据库语义检索失败，已明确降级；未访问其他账户资料'
    elif settings:
        old=retrieve(sources,query,settings)
        mode,note=old['mode'],old['note']
        for i,p in enumerate(old['chunks'],1):scores[p['ref']]=scores.get(p['ref'],0)+1/(60+i)
    ranked=sorted((p for p in pieces if p['ref'] in scores),key=lambda p:(-scores[p['ref']],p['ref']))[:12]
    selected=assemble(ranked,query,budget)
    for item in selected:
        source=sources[int(item['ref'].split(':')[0][1:])-1]
        item['source_version']=source.get('updated_at')
        item['index_id']=source.get('index_id')
        item['content_hash']=(source.get('chunk_index') or {}).get('hash')
    return {'mode':mode,'note':note,'chunks':selected,'context_version':VERSION,
        'body_chars':sum(len(p['text']) for p in selected),'body_budget':budget,
        'evidence_status':'unassessed','warning':'检索命中不表示证据充分；版本日期不表示事实更可信'}
