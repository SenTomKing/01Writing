"""检索实验的确定性部分：切分、排序与原文证据评分。"""
from collections import Counter
import math
import re

from writing_server.knowledge import terms
from writing_server.retrieval import split_content

SPLITTERS = {
    'fixed300': {'mode': 'fixed', 'size': 300, 'overlap': 0},
    'boundary300_60': {'mode': 'boundary', 'size': 300, 'overlap': 60},
    'boundary600_90': {'mode': 'boundary', 'size': 600, 'overlap': 90},
    'boundary900_0': {'mode': 'boundary', 'size': 900, 'overlap': 0},
    'boundary900_120': {'mode': 'boundary', 'size': 900, 'overlap': 120},
}


def split_documents(documents, config):
    result = {}
    for doc in documents:
        text = doc['content']
        if config['mode'] == 'fixed':
            pieces = [{'offset': i, 'text': text[i:i+config['size']]} for i in range(0, len(text), config['size'])]
        else:
            pieces = split_content(text, max_chars=config['size'], overlap=config['overlap'])
        result[doc['id']] = [{'doc_id': doc['id'], 'title': doc['title'],
            'start': p['offset'], 'end': p['offset']+len(p['text']), 'text': p['text']} for p in pieces]
    return result


def scoped_pieces(by_doc, source_ids):
    return [dict(p, ref=f"S{i}:{p['start']}") for i, doc in enumerate(source_ids, 1) for p in by_doc[doc]]


def tokens(text):
    text = text.casefold()
    result = re.findall(r'[a-z0-9_]+', text)
    for segment in re.findall(r'[\u4e00-\u9fff]+', text):
        result.extend(segment[i:i+2] for i in range(max(1, len(segment)-1)))
    return result


def lexical_scores(pieces, query, mode):
    if mode == 'keyword':
        q = terms(query)
        return [len(q & terms(p['title']+' '+p['text'])) for p in pieces]
    docs = [Counter(tokens(p['title']+' '+p['text'])) for p in pieces]
    q = set(tokens(query))
    n = len(docs)
    avg = sum(sum(d.values()) for d in docs) / max(1, n)
    frequencies = {t: sum(t in d for d in docs) for t in q}
    scores = []
    for d in docs:
        length = sum(d.values())
        score = 0.
        for t in q:
            tf = d[t]
            if tf:
                df = frequencies[t]
                idf = math.log(1+(n-df+0.5)/(df+0.5))
                score += idf * tf * 2.2 / (tf+1.2*(0.25+0.75*length/avg))
        scores.append(score)
    return scores


def rank(pieces, query, method, semantic=None):
    def lexical(mode):
        scores = lexical_scores(pieces, query, mode)
        return sorted((i for i,s in enumerate(scores) if s > 0), key=lambda i: (-scores[i], pieces[i]['ref']))
    if method in {'keyword', 'bm25'}:
        return lexical(method)
    dense = sorted(range(len(pieces)), key=lambda i: (semantic[i], pieces[i]['ref']), reverse=True)
    if method == 'dense':
        return dense
    lex = lexical('keyword' if method == 'rrf_keyword' else 'bm25')
    # rrf_keyword 与当前产品相同：全部非零关键词结果融合前10个向量结果。
    if method == 'rrf_bm25':
        lex = lex[:10]
    scores = {i: 1/(60+r) for r,i in enumerate(lex, 1)}
    for r,i in enumerate(dense[:10], 1):
        scores[i] = scores.get(i, 0) + 1/(60+r)
    return sorted(scores, key=lambda i: (-scores[i], pieces[i]['ref']))


def merged(intervals):
    result = []
    for a,b in sorted(intervals):
        if result and a <= result[-1][1]:
            result[-1] = (result[-1][0], max(b, result[-1][1]))
        else:
            result.append((a,b))
    return result


def covered(gold, pieces):
    intervals = merged((p['start'],p['end']) for p in pieces if p['doc_id'] == gold['doc_id'])
    return any(a <= gold['start'] and b >= gold['end'] for a,b in intervals)


def budget_context(pieces, budget=1500):
    result = []
    for piece in pieces:
        if budget <= 0:
            break
        take = min(piece['end']-piece['start'], budget)
        result.append(dict(piece, end=piece['start']+take))
        budget -= take
    return result


def score(gold, ranked, k=5):
    chosen = ranked[:k]
    chars = sum(p['end']-p['start'] for p in chosen)
    unique = sum(sum(b-a for a,b in merged((p['start'],p['end']) for p in chosen if p['doc_id']==doc))
                 for doc in {p['doc_id'] for p in chosen})
    result = {'context_chars':chars, 'redundancy':1-unique/chars if chars else 0., 'returned':len(chosen)}
    if not gold:
        return result | {'no_answer_returned':float(bool(chosen))}
    for cutoff in (1,3,5):
        hits = sum(covered(g, ranked[:cutoff]) for g in gold)
        result[f'evidence_recall@{cutoff}'] = hits/len(gold)
    hits = sum(covered(g, chosen) for g in gold)
    result['hit@5'] = float(hits > 0)
    result['all_evidence@5'] = float(hits == len(gold))
    result['mrr@5'] = next((1/r for r in range(1,6) if any(covered(g,ranked[:r]) for g in gold)), 0.)
    result['precision@5'] = sum(any(g['doc_id']==p['doc_id'] and max(g['start'],p['start']) < min(g['end'],p['end']) for g in gold) for p in chosen)/5
    result['evidence_recall_budget1500'] = sum(covered(g,budget_context(chosen)) for g in gold)/len(gold)
    return result


def validate_dataset(data):
    docs = {d['id']:d for d in data['documents']}
    assert len(docs) == len(data['documents'])
    ids, families = set(), {}
    for q in data['queries']:
        assert q['id'] not in ids
        ids.add(q['id'])
        assert q['split'] in {'dev','test'}
        assert families.setdefault(q['family'],q['split']) == q['split']
        assert len(q['source_ids']) == len(set(q['source_ids'])) <= 8
        assert all(i in docs for i in q['source_ids'])
        for g in q['gold']:
            assert g['doc_id'] in q['source_ids']
            assert docs[g['doc_id']]['content'][g['start']:g['end']] == g['quote']
            assert g['quote'] and g['start'] >= 0
    return True
