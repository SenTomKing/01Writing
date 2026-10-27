"""固定位置片段、字词匹配与可选向量排名融合；不把降级说成语义检索。"""
import hashlib
import math


def split_content(content, pages=()):
    result, offset = [], 0
    while offset < len(content):
        end = min(offset+900, len(content))
        page_end = next((p['offset'] for p in pages if offset < p['offset'] < end), None)
        if page_end is not None:
            end = page_end
        elif end < len(content):
            boundary = content.rfind('\n', offset+350, end)
            if boundary > offset:
                end = boundary+1
        page = next((p['page'] for p in reversed(pages) if p['offset'] <= offset), None)
        result.append({'offset': offset, 'text': content[offset:end], 'page': page})
        offset = end
    return result


def embedding_id(settings):
    if not settings.embedding_key or not settings.embedding_model or not settings.embedding_url:
        return ''
    return hashlib.sha256((settings.embedding_url.rstrip('/')+'|'+settings.embedding_model).encode()).hexdigest()[:20]


def embed(settings, texts):
    from openai import OpenAI
    with OpenAI(api_key=settings.embedding_key, base_url=settings.embedding_url, timeout=20, max_retries=0) as client:
        response = client.embeddings.create(model=settings.embedding_model, input=texts)
    if len(response.data) != len(texts):
        raise ValueError('向量服务返回数量错误')
    ordered = sorted(response.data, key=lambda row: row.index)
    if [row.index for row in ordered] != list(range(len(texts))):
        raise ValueError('向量索引不完整')
    vectors = [row.embedding for row in ordered]
    dim = len(vectors[0])
    if not 1 <= dim <= 4096 or any(len(v) != dim or not all(math.isfinite(x) for x in v) or not any(v) for v in vectors):
        raise ValueError('向量维度或数值不合法')
    return vectors


def build_index(settings, content, pages=()):
    pieces = split_content(content, pages)
    fingerprint = hashlib.sha256(content.encode()).hexdigest()
    identity = embedding_id(settings)
    if identity:
        for offset in range(0, len(pieces), 32):
            batch = pieces[offset:offset+32]
            vectors = embed(settings, [p['text'] for p in batch])
            for piece, vector in zip(batch, vectors, strict=True):
                piece['vector'] = vector
    return {'version': 1, 'hash': fingerprint, 'embedding_id': identity, 'chunks': pieces}


def cosine(a, b):
    if len(a) != len(b):
        return -1
    return sum(x*y for x,y in zip(a,b)) / (math.sqrt(sum(x*x for x in a))*math.sqrt(sum(x*x for x in b)))


def retrieve(sources, query, settings=None):
    from .knowledge import terms, chunks
    pieces = list(chunks(sources))
    q = terms(query)
    lexical = sorted(((len(q & terms(p['title']+' '+p['text'])), p) for p in pieces), key=lambda x: (-x[0], x[1]['ref']))
    ranked = [p for score,p in lexical if score]
    scores = {p['ref']: 1/(60+i) for i,p in enumerate(ranked, 1)}
    mode, note = 'keyword', '关键词检索；未启用匹配的语义索引'
    identity = embedding_id(settings) if settings else ''
    vectors = {}
    for i,source in enumerate(sources, 1):
        index = source.get('chunk_index') or {}
        if identity and index.get('embedding_id') == identity:
            vectors.update({f"S{i}:{p['offset']}": p['vector'] for p in index.get('chunks', []) if p.get('vector')})
    if vectors:
        try:
            vector = embed(settings, [query])[0]
            semantic = sorted(((cosine(vector,v), ref) for ref,v in vectors.items()), reverse=True)[:10]
            for i,(_,ref) in enumerate(semantic,1):
                scores[ref] = scores.get(ref,0) + 1/(60+i)
            mode, note = 'hybrid', '关键词与语义排名融合（RRF）'
        except Exception:
            note = '语义服务不可用，已降级为关键词检索'
    selected = sorted((p for p in pieces if p['ref'] in scores), key=lambda p: (-scores[p['ref']], p['ref']))[:5]
    return {'mode': mode, 'note': note, 'chunks': selected}
